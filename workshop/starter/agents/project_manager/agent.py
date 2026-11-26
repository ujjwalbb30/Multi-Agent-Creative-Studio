import asyncio
import datetime
import json
import logging
import os
import re
from typing import Optional

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.base_toolset import BaseToolset
from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from google.adk.tools.tool_context import ToolContext
from dotenv import load_dotenv
from mcp import StdioServerParameters
try:
    from .retry import GENERATE_CONTENT_CONFIG
except ImportError:
    from retry import GENERATE_CONTENT_CONFIG

load_dotenv()

logger = logging.getLogger("ai_creative_studio.project_manager")

GEMINI_MODEL = os.getenv("GEMINI_MODEL")
if not GEMINI_MODEL:
    raise RuntimeError(
        "GEMINI_MODEL is not set. Copy workshop/starter/.env.example to .env "
        "and set GEMINI_MODEL, or pass it as an env var when deploying."
    )

# After this many Notion errors in one run, the hint tells the model to stop using
# Notion and report it; the text timeline is still produced.
MAX_NOTION_ERRORS = 3
NOTION_ERROR_KEY_PREFIX = "pm_notion_error:"
NOTION_SCHEMA_KEY_PREFIX = "pm_notion_schema:"
NOTION_REPAIR_KEY_PREFIX = "pm_notion_repair:"
PAGE_WRITE_TOOLS = ("API-post-page", "API-patch-page")
_NAME_STRIP = " \t\r\n\"'\\"
NOTION_AVAILABLE_KEY = "pm_notion_available"
NOTION_PROBE_TIMEOUT = 30.0

_UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}\b")
# Prefixes are split so the repo's secret scan never matches this source file.
_TOKEN_RE = re.compile("(" + "ntn" + "_|" + "secret" + "_" + r")[A-Za-z0-9]+|" + "Bear" + r"er\s+\S+")


def mask_id(value: Optional[str]) -> str:
    """Shows only the last 4 characters of an ID."""
    return f"…{value[-4:]}" if value else "(unset)"


def mask_text(text: str) -> str:
    """Masks IDs and tokens in text before it is logged."""
    text = _TOKEN_RE.sub("<redacted>", text)
    return _UUID_RE.sub(lambda m: mask_id(m.group(0)), text)


def build_notion_server_params(notion_token: str) -> StdioServerParameters:
    """notion-mcp-server, with its stderr discarded.

    On a failed HTTP call the server writes the whole axios error to stderr,
    including the Authorization header with the token. On Cloud Run stderr goes
    to Cloud Logging, so it must never reach our process. Our own masked error
    lines come from handle_notion_error instead.
    """
    return StdioServerParameters(
        command="sh",
        args=["-c", "exec notion-mcp-server 2>/dev/null"],
        env={"NOTION_TOKEN": notion_token, "PATH": os.environ.get("PATH", "")},
    )


class GuardedNotionToolset(BaseToolset):
    """Wraps the Notion McpToolset so an unavailable server never crashes a run.

    If the server cannot start or exposes no tools, get_tools returns [] and the
    agent reports "Notion unavailable" in its Notion Status.
    """

    def __init__(self, inner: McpToolset):
        super().__init__()
        self._inner = inner

    async def get_tools(self, readonly_context: Optional[ReadonlyContext] = None) -> list[BaseTool]:
        try:
            tools = await asyncio.wait_for(self._inner.get_tools(readonly_context), NOTION_PROBE_TIMEOUT)
        except Exception as e:
            logger.warning("Notion MCP server unavailable: %s", mask_text(f"{type(e).__name__}: {e}"))
            return []
        if not tools:
            logger.warning("Notion MCP server exposed no tools")
        return tools

    async def close(self) -> None:
        await self._inner.close()


def _response_text(tool_response) -> str:
    if isinstance(tool_response, dict):
        content = tool_response.get("content") or []
    else:
        content = getattr(tool_response, "content", None) or []
    first = content[0] if content else {}
    return (first.get("text") if isinstance(first, dict) else getattr(first, "text", "")) or ""


def _norm_id(value) -> str:
    return str(value or "").replace("-", "").lower()


def _run_values(tool_context: ToolContext, prefix: str) -> list:
    """Values stored under one state key per call for the current run (invocation)."""
    run_prefix = f"{prefix}{tool_context.invocation_id}:"
    return [v for k, v in tool_context.state.to_dict().items() if k.startswith(run_prefix) and v]


def record_notion_schema(
    tool: BaseTool, args: dict, tool_context: ToolContext, tool_response: dict
) -> dict | None:
    """Remembers each database's property names from API-retrieve-a-database."""
    if tool.name != "API-retrieve-a-database":
        return None
    try:
        data = json.loads(_response_text(tool_response))
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("object") != "database":
        return None
    names = sorted((data.get("properties") or {}).keys())
    # Only the last 8 characters of the ID are kept in state.
    key = f"{NOTION_SCHEMA_KEY_PREFIX}{tool_context.invocation_id}:{tool_context.function_call_id}"
    tool_context.state[key] = {"db": _norm_id(data.get("id") or args.get("database_id"))[-8:], "names": names}
    return None


def schema_names(tool_context: ToolContext, database_id=None) -> list[str]:
    """Property names recorded this run, for one database if known, else for all."""
    schemas = _run_values(tool_context, NOTION_SCHEMA_KEY_PREFIX)
    if database_id:
        match = [s["names"] for s in schemas if s["db"] == _norm_id(database_id)[-8:]]
        if match:
            return match[-1]
    return sorted({n for s in schemas for n in s["names"]})


def make_notion_arg_repair(database_ids: tuple[str, ...]):
    """Builds the before_tool_callback that repairs two known model mistakes.

    1. parent.page_id equal to a configured database ID -> parent.database_id
       (a database can never be a page parent).
    2. A key under "properties" that is not in the recorded schema but matches a
       schema name after stripping quotes, backslashes and whitespace from its ends
       is renamed to that exact name.
    Nothing else is changed: no other names are guessed and no values are touched.
    Each repair is logged (masked) and recorded in state.
    """
    configured = {_norm_id(d) for d in database_ids if d}

    def repair_notion_args(tool: BaseTool, args: dict, tool_context: ToolContext) -> dict | None:
        if tool.name not in PAGE_WRITE_TOOLS or not isinstance(args, dict):
            return None
        repairs = []
        parent = args.get("parent")
        if isinstance(parent, dict) and "page_id" in parent and _norm_id(parent["page_id"]) in configured:
            parent["database_id"] = parent.pop("page_id")
            repairs.append(f"parent page_id -> database_id ({mask_id(str(parent['database_id']))})")

        props = args.get("properties")
        if isinstance(props, dict) and props:
            db = parent.get("database_id") if isinstance(parent, dict) else None
            names = set(schema_names(tool_context, db))
            renamed = {}
            for key in list(props):
                if key in names:
                    renamed[key] = props[key]
                    continue
                stripped = key.strip(_NAME_STRIP)
                if stripped != key and stripped in names and stripped not in props:
                    renamed[stripped] = props[key]
                    repairs.append(f"property {key!r} -> {stripped!r}")
                else:
                    renamed[key] = props[key]
            if renamed != props or list(renamed) != list(props):
                args["properties"] = renamed

        if repairs:
            logger.warning("Repaired %s arguments: %s", tool.name, mask_text("; ".join(repairs)))
            key = f"{NOTION_REPAIR_KEY_PREFIX}{tool_context.invocation_id}:{tool_context.function_call_id}"
            tool_context.state[key] = repairs
        return None

    return repair_notion_args


def notion_hint(status: int, code: str, message: str, valid_names: Optional[list[str]] = None) -> str:
    """Actionable recovery hint for a Notion API error."""
    if status == 401:
        return ("The Notion token is invalid or revoked. Stop using Notion tools for this run and "
                "report 'Notion unavailable: invalid token' in the Notion Status.")
    if status == 404:
        return ("Notion could not find the object. Two common causes: (1) a database ID was passed "
                'as page_id - create pages with {"parent": {"database_id": "<id>"}}, never page_id; '
                "(2) the database or page is not shared with the integration (in Notion: ... > "
                "Connections). Do not retry the same call unchanged.")
    if status == 400:
        lowered = message.lower()
        if "people" in lowered or "person" in lowered or "user" in lowered:
            return ('Do not set "people" properties (e.g. Owner, Assignee): integration tokens cannot '
                    "assign users. Retry without them; put the owner's name in the title or text instead.")
        if "relation" in lowered:
            return ('Set a relation only as {"relation": [{"id": "<page-id>"}]}. If it still fails, '
                    "retry without the relation property.")
        if "is not a property that exists" in lowered and valid_names:
            return ("That property name does not exist. Valid property names for this database are: "
                    + ", ".join(valid_names) + ". Use them exactly as written, without extra quotes "
                    "or escaping. Retry with corrected parameters.")
        return ("Use only property names and types that exist in the schema you retrieved "
                "(status properties take {\"status\": {\"name\": ...}}, selects take "
                "{\"select\": {\"name\": ...}} with an existing option). Retry with corrected parameters.")
    if status == 429 or status >= 500:
        return "Notion is busy or failing. Retry this call once; if it fails again, stop using Notion."
    return "Retry once with corrected parameters."


def handle_notion_error(
    tool: BaseTool,
    args: dict,
    tool_context: ToolContext,
    tool_response: dict,
) -> dict | None:
    """Replaces a raw Notion error with a short, actionable recovery hint.

    The MCP response's isError flag is not reliable (it is False even for a 404),
    so the JSON status inside the text is used. Each error is counted under its
    own state key (parallel tool calls each see a separate copy of the state);
    after MAX_NOTION_ERRORS the hint tells the model to stop using Notion.
    """
    if not tool.name.startswith("API-"):
        return None
    try:
        data = json.loads(_response_text(tool_response))
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("object") != "error":
        return None

    status = int(data.get("status") or 0)
    code = str(data.get("code", ""))
    message = str(data.get("message", ""))
    parent = args.get("parent") if isinstance(args, dict) else None
    db = parent.get("database_id") if isinstance(parent, dict) else None
    valid = schema_names(tool_context, db) if status == 400 else None
    return _error_response(tool, tool_context, status, code, message,
                           notion_hint(status, code, message, valid))


def handle_notion_exception(
    tool: BaseTool,
    args: dict,
    tool_context: ToolContext,
    error: Exception,
) -> dict | None:
    """Turns a raised Notion tool error (e.g. a network failure) into a hint, not a crash."""
    if not tool.name.startswith("API-"):
        return None
    return _error_response(
        tool, tool_context, 0, type(error).__name__, str(error),
        "The Notion call failed before Notion answered. Retry this call once; if it fails again, "
        "stop using Notion and report it in the Notion Status.")


def _error_response(tool: BaseTool, tool_context: ToolContext, status: int, code: str,
                    message: str, hint: str) -> dict:
    """Logs a masked line, counts the error for this run, and returns the hint."""
    logger.warning("Notion %s (%s) on %s: %s", status or "error", code, tool.name, mask_text(message)[:300])

    key = f"{NOTION_ERROR_KEY_PREFIX}{tool_context.invocation_id}:{tool_context.function_call_id}"
    tool_context.state[key] = status or -1
    prefix = f"{NOTION_ERROR_KEY_PREFIX}{tool_context.invocation_id}:"
    errors = sum(1 for k, v in tool_context.state.to_dict().items() if k.startswith(prefix) and v)

    if errors >= MAX_NOTION_ERRORS:
        hint = (f"This run has hit {errors} Notion errors. Stop using Notion tools now, finish the text "
                "timeline, and report what was and was not created in the Notion Status.")
    label = f"Notion {status} ({code})" if status else f"Notion call failed ({code})"
    return {
        "content": [{
            "type": "text",
            "text": f"{label} on {tool.name}: {mask_text(message)[:300]}\n\n{hint}",
        }]
    }


def get_system_instruction(project_database_id=None, tasks_database_id=None, notion_available=True):
    # notion_section is empty when Notion is not configured, so the agent
    # receives no tool instructions for capabilities it doesn't have.
    if project_database_id and not notion_available:
        notion_section = """
Notion is configured but unavailable for this run (its MCP server did not start or exposed no
tools). Do not attempt any Notion operation. Set Notion Status to:
"Notion unavailable - text timeline only".
"""
    elif project_database_id:
        notion_section = f"""
Projects database ID: {project_database_id}
Tasks database ID: {tasks_database_id}

Also persist the project and tasks to these Notion databases using the available Notion tools.
Notion tools follow the pattern `API-<operation>` — use their exact names as listed in the tool
manifest. Use them directly — never wrap in `print()` or prefix with `default_api.`

Before creating anything, use the available tools to discover the schema of each database.
Only use property names and types that actually exist in the schema you discover.

Property rules:
- Always set the database parent using `database_id` — never `page_id`
- Never set "people" or "person" type properties — integration tokens cannot assign users; skip them
- For "relation" type properties linking tasks to the project: set ONLY {{"relation": [{{"id": "<project-page-id>"}}]}}.
  Never set sub-fields like name, state, start, lat on the relation - those are read-only rollups.
  If a task creation fails with a validation_error on a relation property, immediately retry
  creating that task WITHOUT the relation property entirely.
- Only set properties whose type you can identify from the schema response; if a property type
  is unclear after reading the schema, skip it and note it in the Notion Status
- Since owners cannot be set as people, put the owner in the task title, e.g. "[Copywriter] ..."
- Write property names exactly as they appear in the schema, as plain JSON keys: no extra
  quote marks and no backslashes inside the key. For example, if the schema has a title
  property called Name and a status property called Status, create a page with:
  {{"parent": {{"database_id": "<the database id>"}},
    "properties": {{"Name": {{"title": [{{"text": {{"content": "Launch plan"}}}}]}},
                    "Status": {{"status": {{"name": "Planning"}}}}}}}}

If any Notion call fails, continue — the text timeline is always the primary deliverable.
Write your complete response AFTER all Notion operations are done (or have failed).
In the Notion Status, say what was created (project name and number of tasks) and what failed.
Notion IS configured for this run: never write "Notion not configured" in the Notion Status. If
Notion operations failed, say "Notion sync failed" and what failed.
Never include database IDs, page IDs or URLs in your response.

If image HTTPS links are provided in the input (under "Generated Images" from the Creative
Director), add them to the Notion project page body as a bulleted list under a
"Generated Images" heading after creating the project page.
"""
    else:
        notion_section = ""

    today = datetime.date.today()
    return f"""You are a Project Manager for Instagram campaigns.

You receive an APPROVED campaign (research, captions, visuals and the Critic's verdict) and turn
it into an executable plan. Do not rewrite the captions or redesign the visuals; plan their delivery.

Today's date is {today.strftime("%B %d, %Y")} ({today.isoformat()}). Every date in the plan starts
from today, in the future, written as YYYY-MM-DD.

Plan the campaign in four phases, in this order: Strategy, Creation, Review, Launch.
Create tasks with an owner (a role such as Brand Strategist, Copywriter, Designer, Critic, Project
Manager, Community Manager) and a deadline. Include one publishing task per approved caption,
naming the caption's title, and reference the approved visuals by their concept names.

The text plan is the primary deliverable and must ALWAYS be complete, whatever happens with Notion.
Use EXACTLY these five sections, in this order:

**Project Timeline:**
- [Phase]: [start YYYY-MM-DD] to [end YYYY-MM-DD] - [what happens]

**Task List:**
| Task | Owner | Deadline | Status |
|---|---|---|---|
| [task] | [role] | [YYYY-MM-DD] | [Not Started / In Progress / Done] |

**Budget Breakdown:**
- [Category]: [amount in USD] - [rationale]
- Total: [amount in USD]

**Milestones:**
- [YYYY-MM-DD]: [checkpoint]

**Notion Status:**
[What was created in Notion, or "Notion not configured - text timeline only"]
{notion_section}"""


def create_project_manager_agent():
    """Create the Project Manager agent, with Notion MCP if credentials are set."""
    notion_token           = os.getenv("NOTION_TOKEN")
    notion_project_db_id   = os.getenv("NOTION_PROJECT_DATABASE_ID")
    notion_tasks_db_id     = os.getenv("NOTION_TASKS_DATABASE_ID")

    description = ("Project Manager: turns an approved Instagram campaign into a dated timeline, task "
                   "list, budget and milestones; optionally syncs the project and tasks to Notion via MCP.")

    if not notion_token or not notion_project_db_id or not notion_tasks_db_id:
        logger.warning("Notion credentials not set — running without Notion integration")
        return Agent(
            name="project_manager",
            model=GEMINI_MODEL,
            generate_content_config=GENERATE_CONTENT_CONFIG,
            # Built per request so the date never goes stale on a long-running container.
            instruction=lambda ctx: get_system_instruction(),
            description=description,
        )

    logger.info("Notion configured — projects database %s, tasks database %s",
                mask_id(notion_project_db_id), mask_id(notion_tasks_db_id))
    notion_toolset = GuardedNotionToolset(McpToolset(
        connection_params=StdioConnectionParams(
            server_params=build_notion_server_params(notion_token), timeout=30.0)
    ))

    async def check_notion_available(callback_context: CallbackContext) -> None:
        """Run-start check: does the Notion server expose its tools?"""
        tools = await notion_toolset.get_tools()
        callback_context.state[NOTION_AVAILABLE_KEY] = bool(tools)
        return None

    def instruction(ctx: ReadonlyContext) -> str:
        return get_system_instruction(
            project_database_id=notion_project_db_id,
            tasks_database_id=notion_tasks_db_id,
            notion_available=ctx.state.get(NOTION_AVAILABLE_KEY, True),
        )

    return Agent(
        name="project_manager",
        model=GEMINI_MODEL,
        generate_content_config=GENERATE_CONTENT_CONFIG,
        before_agent_callback=check_notion_available,
        before_tool_callback=make_notion_arg_repair((notion_project_db_id, notion_tasks_db_id)),
        after_tool_callback=[record_notion_schema, handle_notion_error],
        on_tool_error_callback=handle_notion_exception,
        instruction=instruction,
        description=description,
        tools=[notion_toolset],
    )


root_agent = create_project_manager_agent()
logger.info("Project Manager agent created")


if __name__ == "__main__":
    import uvicorn
    from google.adk.a2a.utils.agent_to_a2a import to_a2a

    PORT = int(os.getenv("PORT", "8080"))
    HOST = os.getenv("HOST", "0.0.0.0")
    PUBLIC_HOST = os.getenv("PUBLIC_HOST", "localhost")
    PUBLIC_PORT = int(os.getenv("PUBLIC_PORT", str(PORT)))
    PROTOCOL = os.getenv("PROTOCOL", "http")

    a2a_app = to_a2a(root_agent, host=PUBLIC_HOST, port=PUBLIC_PORT, protocol=PROTOCOL)

    logger.info(f"Starting Project Manager on {PROTOCOL}://{HOST}:{PORT}")
    logger.info(f"Agent card: {PROTOCOL}://{HOST}:{PORT}/.well-known/agent.json")

    uvicorn.run(a2a_app, host=HOST, port=PORT)
