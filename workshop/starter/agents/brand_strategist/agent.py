import datetime
import logging
import os
from typing import Optional

from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models import LlmResponse
from google.adk.tools.google_search_tool import google_search
from google.genai import types
try:
    from .retry import GENERATE_CONTENT_CONFIG
except ImportError:
    from retry import GENERATE_CONTENT_CONFIG

load_dotenv()

logger = logging.getLogger("ai_creative_studio.brand_strategist")

GEMINI_MODEL = os.getenv("GEMINI_MODEL")
if not GEMINI_MODEL:
    raise RuntimeError(
        "GEMINI_MODEL is not set. Copy workshop/starter/.env.example to .env "
        "and set GEMINI_MODEL, or pass it as an env var when deploying."
    )

SYSTEM_INSTRUCTION_TEMPLATE = """You are a Brand Strategist on an Instagram campaign team.
Your job is RESEARCH ONLY. The Creative Director coordinates what happens next.

Today's date is {today}. The current year is {year}.

## How to research
- Before writing anything, call google_search at least once for EACH of the three
  research areas below (audience, competitors, trends). Do not answer from memory.
- Keep searching focused: 2-3 searches per research area, at most 9 in total.
  Combine related questions into one query instead of searching for each detail.
- ALWAYS include the current year ({year}) in every search query,
  e.g. "Gen Z sustainable products trends {year}".
- Never cite a source, report, statistic, or brand fact that you did not get from
  google_search results in this conversation.
- If search returns nothing useful for an area, say so in that section
  (e.g. "No reliable {year} data found for ...") instead of filling the gap.
- Prefer sources from {year}. If you rely on an older source, say how old it is.

## What to research
1. Target audience: demographics, motivations, pain points, and how they use Instagram.
2. 2-3 competitor brands: their positioning, content style, and the gaps they leave open.
3. 3-5 trending topics, formats, or conversations in the product category right now.

## Output format
Return exactly these four sections, in this order, with these exact labels:

**Audience Insights:**
...

**Competitive Analysis:**
...

**Trending Topics:**
...

**Key Strategic Insights:**
...

Use short bullet points with concrete facts, names, and numbers where you found them.

## Scope rules (strict)
- DO NOT write captions, hashtags, slogans, taglines, ad copy, or post text.
- DO NOT propose visuals, image prompts, layouts, or designs.
- If the user asks for a caption, copy, or a design, do not produce it. Say in one line
  that the Copywriter or Designer handles that, then deliver the four research sections
  for the brand or product in the request.
"""


def build_instruction(context: ReadonlyContext) -> str:
    """Builds the instruction on every request so the date never goes stale.

    A plain string instruction would freeze the date at import time on a
    long-running Cloud Run container, and ADK would treat {braces} in it as
    session-state placeholders.
    """
    today = datetime.date.today()
    return SYSTEM_INSTRUCTION_TEMPLATE.format(
        today=today.strftime("%B %d, %Y"), year=today.year
    )


UNGROUNDED_WARNING = (
    "\n\n⚠ No Google Search results were used for this response; "
    "treat it as unverified."
)


FAILED_RESEARCH_MESSAGE = (
    "⚠ Research failed ({reason}). No results were produced; "
    "ask again with a narrower brief."
)


def flag_missing_grounding(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> Optional[LlmResponse]:
    """Makes failed or ungrounded answers visible instead of silent.

    - Error with no text (e.g. TOO_MANY_TOOL_CALLS): replace the empty answer
      with a short failure message. error_code is kept for logs and callers.
    - Final text with no grounding metadata: append a warning.
    Only flags; it does not retry or change the research itself.
    """
    if llm_response.partial:
        return None
    has_text = bool(
        llm_response.content
        and llm_response.content.parts
        and any(part.text and not part.thought for part in llm_response.content.parts)
    )
    if llm_response.error_code and not has_text:
        logger.warning("Brand Strategist model call failed: %s", llm_response.error_code)
        llm_response.content = types.Content(
            role="model",
            parts=[types.Part(text=FAILED_RESEARCH_MESSAGE.format(reason=llm_response.error_code))],
        )
        return llm_response
    if not llm_response.content or not llm_response.content.parts:
        return None
    if not any(part.text and not part.thought for part in llm_response.content.parts):
        return None
    grounding = llm_response.grounding_metadata
    if grounding and grounding.web_search_queries:
        return None
    logger.warning("Brand Strategist response has no grounding metadata")
    llm_response.content.parts.append(types.Part(text=UNGROUNDED_WARNING))
    return llm_response


root_agent = Agent(
    name="brand_strategist",
    model=GEMINI_MODEL,
    generate_content_config=GENERATE_CONTENT_CONFIG,
    instruction=build_instruction,
    description=(
        "Research-only Brand Strategist: uses Google Search to return audience "
        "insights, competitive analysis, trending topics, and key strategic "
        "insights for an Instagram campaign. Does not write copy or designs."
    ),
    tools=[google_search],
    after_model_callback=flag_missing_grounding,
)

logger.info("Brand Strategist agent created")


if __name__ == "__main__":
    import uvicorn
    from google.adk.a2a.utils.agent_to_a2a import to_a2a

    PORT = int(os.getenv("PORT", "8082"))
    HOST = os.getenv("HOST", "0.0.0.0")
    PUBLIC_HOST = os.getenv("PUBLIC_HOST", "localhost")
    PUBLIC_PORT = int(os.getenv("PUBLIC_PORT", str(PORT)))
    PROTOCOL = os.getenv("PROTOCOL", "http")

    a2a_app = to_a2a(root_agent, host=PUBLIC_HOST, port=PUBLIC_PORT, protocol=PROTOCOL)

    logger.info(f"Starting Brand Strategist on {PROTOCOL}://{HOST}:{PORT}")
    logger.info(f"Agent card: {PROTOCOL}://{HOST}:{PORT}/.well-known/agent.json")

    uvicorn.run(a2a_app, host=HOST, port=PORT)
