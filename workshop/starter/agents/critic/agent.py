import logging
import os
import re
from typing import Any, Optional

from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmResponse
from google.adk.tools import BaseTool, FunctionTool, ToolContext
from google.genai import types
try:
    from .retry import GENERATE_CONTENT_CONFIG
    from .image_review_tool import review_image
    from .verdict import VerdictParseError, enforce
except ImportError:
    from retry import GENERATE_CONTENT_CONFIG
    from image_review_tool import review_image
    from verdict import VerdictParseError, enforce

load_dotenv()

logger = logging.getLogger("ai_creative_studio.critic")

SYSTEM_INSTRUCTION = """You are a Creative Director and Quality Assurance Specialist.

Your role: Review Instagram campaign materials and provide structured, actionable feedback.

CRITICAL: You MUST use the EXACT output format below. The orchestrator parses your response
programmatically - any deviation will break the revision workflow.

Scoring guide:
- 9-10: APPROVED (exceptional, publish as-is)
- 7-8:  APPROVED (good, minor polish only)
- 5-6:  NEEDS_REVISION (has potential but needs improvement)
- 1-4:  NEEDS_REVISION (significant issues)

VISUAL REVIEW WITH REAL IMAGES:
When the input contains `gcs_uri` values (from the Designer), you MUST call the
`review_image` tool for each image BEFORE writing your VISUALS REVIEW section.
Pass the GCS URI, the concept name, and a brief campaign context derived from the copy.
The tool returns structured fields: `score`, `approval_status`, `what_works`, `issues`,
`suggestions`. Use these directly - do not re-score or override them. Aggregate across
all concepts (use the lowest score and NEEDS_REVISION if any concept needs revision).
Call `review_image` exactly once for each distinct `gs://` URI in the input.

REVIEW ERRORS:
If `review_image` returns `status: "error"` for an image, count that image as
NEEDS_REVISION and start the VISUALS REVIEW Issues with [REVIEW_ERROR] followed by the
concept name and the error. Do not guess a score for that image.

NO IMAGES PROVIDED:
If the input contains no `gcs_uri` values, write the VISUALS REVIEW section as:
- Score: N/A
- Status: NOT_REVIEWED
- What Works: No images provided for review.
- Issues: None
- Suggestions: None
Treat NOT_REVIEWED as approved when computing All Approved.

OVERALL ASSESSMENT rules:
- All Approved is YES only if POSTS is APPROVED and VISUALS is APPROVED or NOT_REVIEWED.
- Overall Score is the lower of the POSTS score and the VISUALS score (use the POSTS score
  alone when VISUALS is N/A).
- Priority Revisions names the single most important fix when All Approved is NO, or
  "None" when it is YES.

Required output format - use this EXACTLY:

**POSTS REVIEW:**
- Score: [X/10]
- Status: [APPROVED or NEEDS_REVISION]
- What Works: [specific strengths]
- Issues: [specific problems if any]
- Suggestions: [concrete improvements if NEEDS_REVISION]

**VISUALS REVIEW:**
- Score: [X/10 or N/A]
- Status: [APPROVED or NEEDS_REVISION or NOT_REVIEWED]
- What Works: [specific visual strengths from actual image review, or "No images provided for review."]
- Issues: [specific problems if any, or "None"]
- Suggestions: [concrete improvements if NEEDS_REVISION, or "None"]

**OVERALL ASSESSMENT:**
- All Approved: [YES or NO]
- Priority Revisions: [most important fix if All Approved = NO]
- Overall Score: [X/10]

Evaluation criteria:
- Clarity and brand voice consistency
- Audience fit and relevance
- Platform optimization (Instagram best practices)
- Visual-copy alignment
- CTA strength and clarity
- Engagement potential
"""

# One state key per review_image call: ADK runs parallel tool calls concurrently,
# and each call sees its own copy of the state, so appending to one shared list
# would lose results. Keys are scoped to the run (invocation) and the call.
REVIEW_KEY_PREFIX = "critic_review:"
EXPECTED_URIS_KEY = "critic_expected_uris"
GUARD_KEY = "critic_guard"
GCS_URI_RE = re.compile(r"gs://[a-z0-9][a-z0-9._-]*/[^\s)\]\"'`,;*]+")


def reset_review_state(callback_context: CallbackContext) -> None:
    """Starts every Critic run clean; the orchestrator's loop reuses sessions."""
    text = " ".join(
        p.text for p in (callback_context.user_content.parts if callback_context.user_content else [])
        if p.text
    )
    uris = list(dict.fromkeys(u.rstrip(".") for u in GCS_URI_RE.findall(text)))
    for key in list(callback_context.state.to_dict()):
        if key.startswith(REVIEW_KEY_PREFIX):
            callback_context.state[key] = None
    callback_context.state[EXPECTED_URIS_KEY] = uris
    callback_context.state[GUARD_KEY] = None
    return None


def record_review(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext, tool_response: dict
) -> Optional[dict]:
    """Keeps each review_image result so the verdict can be checked against it."""
    if tool.name == "review_image" and isinstance(tool_response, dict):
        result = dict(tool_response)
        result.setdefault("gcs_uri", args.get("gcs_uri"))
        key = f"{REVIEW_KEY_PREFIX}{tool_context.invocation_id}:{tool_context.function_call_id}"
        tool_context.state[key] = result
    return None


def current_reviews(callback_context: CallbackContext) -> list[dict]:
    prefix = f"{REVIEW_KEY_PREFIX}{callback_context.invocation_id}:"
    return [v for k, v in callback_context.state.to_dict().items() if k.startswith(prefix) and v]


def enforce_verdict(callback_context: CallbackContext, llm_response: LlmResponse) -> Optional[LlmResponse]:
    """Corrects the fields derived from review results in the final verdict.

    Acts only on a final text response. If the text does not parse, it is left
    unchanged and logged; the orchestrator treats that as a failed verdict.
    """
    content = llm_response.content
    if llm_response.partial or not content or not content.parts:
        return None
    if any(p.function_call for p in content.parts):
        return None
    text_parts = [p for p in content.parts if p.text and not p.thought]
    if not text_parts:
        return None

    text = "".join(p.text for p in text_parts)
    reviews = current_reviews(callback_context)
    expected = list(callback_context.state.get(EXPECTED_URIS_KEY) or [])
    try:
        fixed, corrections = enforce(text, reviews, expected)
    except VerdictParseError as e:
        logger.warning("Critic verdict could not be parsed; left unchanged: %s", e)
        callback_context.state[GUARD_KEY] = {"parsed": False, "error": str(e), "corrections": []}
        return None

    callback_context.state[GUARD_KEY] = {"parsed": True, "corrections": corrections}
    if not corrections:
        return None
    logger.warning("Critic verdict corrected from review results: %s", corrections)
    thoughts = [p for p in content.parts if p.thought]
    llm_response.content = types.Content(role=content.role, parts=thoughts + [types.Part(text=fixed)])
    return llm_response


# =============================================================================
# PROVIDED — do not modify
#
# Same Agent pattern you used in Step 4. The only difference between
# specialist agents is the name, description, and instruction.
# =============================================================================
root_agent = Agent(
    name="critic",
    model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
    generate_content_config=GENERATE_CONTENT_CONFIG,
    tools=[FunctionTool(func=review_image)],
    instruction=SYSTEM_INSTRUCTION,
    description="Creative critic for reviewing campaign materials and providing constructive feedback",
    # Added in M4: the verdict guard (see verdict.py).
    before_agent_callback=reset_review_state,
    after_tool_callback=record_review,
    after_model_callback=enforce_verdict,
)

logger.info("Critic agent created")


if __name__ == "__main__":
    import uvicorn
    from dotenv import load_dotenv
    from google.adk.a2a.utils.agent_to_a2a import to_a2a

    load_dotenv()

    PORT = int(os.getenv("PORT", "8080"))
    HOST = os.getenv("HOST", "0.0.0.0")
    PUBLIC_HOST = os.getenv("PUBLIC_HOST", "localhost")
    PUBLIC_PORT = int(os.getenv("PUBLIC_PORT", str(PORT)))
    PROTOCOL = os.getenv("PROTOCOL", "http")

    a2a_app = to_a2a(root_agent, host=PUBLIC_HOST, port=PUBLIC_PORT, protocol=PROTOCOL)

    logger.info(f"Starting Critic on {PROTOCOL}://{HOST}:{PORT}")
    logger.info(f"Agent card: {PROTOCOL}://{HOST}:{PORT}/.well-known/agent.json")

    uvicorn.run(a2a_app, host=HOST, port=PORT)
