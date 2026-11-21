import datetime
import logging
import os

from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.google_search_tool import google_search
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
- Use the google_search tool for every research area below. Do not answer from memory.
- ALWAYS include the current year ({year}) in every search query,
  e.g. "Gen Z sustainable products trends {year}".
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
