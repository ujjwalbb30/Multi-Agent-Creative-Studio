"""Offline tests for the Project Manager. No model calls; no real Notion token is used.

1. handle_notion_error: hints for 404 / 400 people / 400 relation / 401 / 429, the
   per-run error cap, success responses untouched, masked logging.
2. Agent branches: no-Notion vs Notion, instruction built per request with today's date.
3. GuardedNotionToolset: an unavailable server gives no tools, not a crash.
4. Leak regression: notion-mcp-server is started with a FAKE token and a failing call
   is made through a dead local proxy (nothing reaches Notion). The captured stdout
   and stderr must not contain the fake token, a bearer header or the full database ID.
   A control run without the stderr wrapper must leak, proving the test can see a leak.
5. check_secrets finds a token-shaped string without printing it.

Usage (from workshop/starter/):
  uv run python scripts/test_m5_pm_offline.py
"""

import asyncio
import datetime
import json
import logging
import os
import pathlib
import subprocess
import sys
import tempfile

STARTER_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STARTER_DIR / "agents"))
sys.path.insert(0, str(STARTER_DIR / "scripts"))

# Fake values only. Built by concatenation so the secret scan does not match this file.
FAKE_TOKEN = "ntn" + "_FAKE" + "0123456789abcdefghijABCDEFGHIJ"
FAKE_PROJECTS_DB = "11111111-2222-3333-4444-555555555555"
FAKE_TASKS_DB = "66666666-7777-8888-9999-000000000000"
BEARER = "Bear" + "er "

FAILURES = []


def expect(name: str, ok: bool, detail: object = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" ({detail})" if detail != "" else ""))
    if not ok:
        FAILURES.append(name)


# ------------------------------------------------------------ leak regression (subprocess)

LEAK_CHILD = r'''
import asyncio, os, sys
sys.path.insert(0, os.environ["AGENTS_DIR"])
os.environ["GEMINI_MODEL"] = "test-model"
for k in ("NOTION_TOKEN", "NOTION_PROJECT_DATABASE_ID", "NOTION_TASKS_DATABASE_ID"):
    os.environ.pop(k, None)
import project_manager.agent as pm
from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from mcp import StdioServerParameters

async def main():
    params = pm.build_notion_server_params(os.environ["FAKE_TOKEN"])
    if os.environ.get("NO_WRAPPER") == "1":
        params = StdioServerParameters(command="notion-mcp-server", args=[], env=dict(params.env))
    # A dead local proxy: the call fails without reaching Notion.
    params.env.update({"HTTPS_PROXY": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9"})
    toolset = McpToolset(connection_params=StdioConnectionParams(server_params=params, timeout=30.0))
    try:
        tools = await asyncio.wait_for(toolset.get_tools(), 60)
        tool = next(t for t in tools if t.name == "API-retrieve-a-database")
        try:
            result = await asyncio.wait_for(
                tool.run_async(args={"database_id": os.environ["FAKE_DB"]}, tool_context=None), 60)
            print("CALL_DONE returned", sorted(result) if isinstance(result, dict) else type(result).__name__)
        except Exception as e:  # a connection failure raises; the agent's on_tool_error_callback handles it
            print("CALL_DONE raised", type(e).__name__)
    finally:
        await toolset.close()

asyncio.run(main())
'''


def run_leak_child(no_wrapper: bool) -> tuple[int, str]:
    env = {**os.environ, "AGENTS_DIR": str(STARTER_DIR / "agents"), "FAKE_TOKEN": FAKE_TOKEN,
           "FAKE_DB": FAKE_TASKS_DB, "NO_WRAPPER": "1" if no_wrapper else "0"}
    for k in ("NOTION_TOKEN", "NOTION_PROJECT_DATABASE_ID", "NOTION_TASKS_DATABASE_ID"):
        env.pop(k, None)
    proc = subprocess.run([sys.executable, "-W", "ignore", "-c", LEAK_CHILD], env=env, cwd=STARTER_DIR,
                          capture_output=True, text=True, timeout=180)
    return proc.returncode, proc.stdout + proc.stderr


def leaked(output: str) -> list[str]:
    found = []
    if FAKE_TOKEN in output:
        found.append("fake token")
    if BEARER in output:
        found.append("bearer header")
    if FAKE_TASKS_DB in output or FAKE_TASKS_DB.replace("-", "") in output:
        found.append("full database ID")
    return found


def test_leak_regression() -> None:
    code, output = run_leak_child(no_wrapper=False)
    done = next((l for l in output.splitlines() if l.startswith("CALL_DONE")), "")
    expect("wrapped server: failing call finished (returned or raised, no hang)", code == 0 and bool(done),
           f"exit {code}, {done}")
    expect("wrapped server: no fake token, bearer header or full database ID in stdout/stderr",
           not leaked(output), f"found: {leaked(output)}" if leaked(output) else "")

    code, output = run_leak_child(no_wrapper=True)
    expect("control (no wrapper) does leak, so the check can detect a leak",
           bool(leaked(output)), f"control found: {leaked(output)}")


# ------------------------------------------------------------ in-process tests

os.environ["GEMINI_MODEL"] = "test-model"
for _k in ("NOTION_TOKEN", "NOTION_PROJECT_DATABASE_ID", "NOTION_TASKS_DATABASE_ID"):
    os.environ.pop(_k, None)
import dotenv  # noqa: E402

dotenv.load_dotenv = lambda *a, **k: False  # never load the real .env in these tests
from project_manager import agent as pm  # noqa: E402


class FakeState(dict):
    def to_dict(self):
        return dict(self)


class FakeToolContext:
    def __init__(self, state, call_id, invocation_id="inv-1"):
        self.state, self.function_call_id, self.invocation_id = state, call_id, invocation_id


def notion_error(status, code, message):
    return {"content": [{"type": "text", "text": json.dumps(
        {"object": "error", "status": status, "code": code, "message": message})}], "isError": False}


TOOL = type("T", (), {"name": "API-post-page"})()


def hint_for(status, code, message, state=None, call="c1"):
    out = pm.handle_notion_error(TOOL, {}, FakeToolContext(state if state is not None else FakeState(), call),
                                 notion_error(status, code, message))
    return out["content"][0]["text"] if out else None


def test_error_hints() -> None:
    h = hint_for(404, "object_not_found",
                 f"Could not find database with ID: {FAKE_TASKS_DB}. Make sure ... shared with your integration")
    expect("404 hint covers database_id parent AND sharing", h and "database_id" in h and "Connections" in h, h)
    expect("404 message has the full ID masked", h and FAKE_TASKS_DB not in h and "…0000" in h)
    h = hint_for(400, "validation_error", "Owner is expected to be people.")
    expect("400 people -> skip people properties", h and "people" in h and "cannot" in h, h)
    h = hint_for(400, "validation_error", "Project.relation should be an array")
    expect("400 relation -> relation shape / drop it", h and "relation" in h and "without" in h, h)
    h = hint_for(400, "validation_error", "Status is not a property that exists.")
    expect("other 400 -> use the discovered schema", h and "schema" in h, h)
    h = hint_for(401, "unauthorized", "API token is invalid.")
    expect("401 -> stop using Notion", h and "Stop using Notion" in h, h)
    h = hint_for(429, "rate_limited", "slow down")
    expect("429 -> retry once then stop", h and "Retry this call once" in h, h)

    ok = {"content": [{"type": "text", "text": json.dumps({"object": "page", "id": FAKE_PROJECTS_DB})}], "isError": False}
    expect("success response left unchanged",
           pm.handle_notion_error(TOOL, {}, FakeToolContext(FakeState(), "c9"), ok) is None)
    other = type("T", (), {"name": "some_other_tool"})()
    expect("non-Notion tool ignored", pm.handle_notion_error(other, {}, FakeToolContext(FakeState(), "c9"),
                                                             notion_error(404, "x", "y")) is None)
    expect("non-JSON text ignored", pm.handle_notion_error(TOOL, {}, FakeToolContext(FakeState(), "c9"),
                                                           {"content": [{"type": "text", "text": "oops"}]}) is None)

    # Parallel calls each get their own copy of the state; one key per call keeps them all.
    base = FakeState()
    for i in range(1, 4):
        copy = FakeState(base)
        text = hint_for(400, "validation_error", "bad", state=copy, call=f"call-{i}")
        base.update(copy)
    expect("third error in a run -> stop-using-Notion hint (cap)", "Stop using Notion tools now" in text, text)
    other_run = FakeState(base)
    t = pm.handle_notion_error(TOOL, {}, FakeToolContext(other_run, "x", invocation_id="inv-2"),
                               notion_error(400, "validation_error", "bad"))["content"][0]["text"]
    expect("errors from another run do not count toward the cap", "Stop using Notion tools now" not in t)


def test_exception_hint() -> None:
    err = RuntimeError(f"connect ECONNREFUSED; {BEARER}{FAKE_TOKEN} db {FAKE_TASKS_DB}")
    out = pm.handle_notion_exception(TOOL, {}, FakeToolContext(FakeState(), "e1"), err)
    text = out["content"][0]["text"] if out else ""
    expect("raised Notion error -> masked hint, not a crash",
           "Notion call failed (RuntimeError)" in text and "Retry this call once" in text
           and FAKE_TOKEN not in text and BEARER not in text and FAKE_TASKS_DB not in text, text)
    other = type("T", (), {"name": "some_other_tool"})()
    expect("raised error from a non-Notion tool is not handled",
           pm.handle_notion_exception(other, {}, FakeToolContext(FakeState(), "e2"), err) is None)


def test_logging_masked() -> None:
    records = []
    handler = logging.Handler()
    handler.emit = lambda r: records.append(r.getMessage())
    pm.logger.addHandler(handler)
    pm.logger.setLevel(logging.DEBUG)
    try:
        hint_for(404, "object_not_found", f"ID {FAKE_TASKS_DB} token {FAKE_TOKEN} {BEARER}{FAKE_TOKEN}")
        os.environ.update({"NOTION_TOKEN": FAKE_TOKEN, "NOTION_PROJECT_DATABASE_ID": FAKE_PROJECTS_DB,
                           "NOTION_TASKS_DATABASE_ID": FAKE_TASKS_DB})
        pm.create_project_manager_agent()
    finally:
        for k in ("NOTION_TOKEN", "NOTION_PROJECT_DATABASE_ID", "NOTION_TASKS_DATABASE_ID"):
            os.environ.pop(k, None)
        pm.logger.removeHandler(handler)
    blob = "\n".join(records)
    expect("logs mask IDs and tokens", records and FAKE_TASKS_DB not in blob and FAKE_PROJECTS_DB not in blob
           and FAKE_TOKEN not in blob and BEARER not in blob, f"{len(records)} log lines")


def test_branches() -> None:
    plain = pm.create_project_manager_agent()
    instr = plain.instruction(None)
    today = datetime.date.today()
    expect("no-Notion branch has no tools", not plain.tools)
    expect("instruction built per request with today's date",
           callable(plain.instruction) and today.isoformat() in instr and today.strftime("%B %d, %Y") in instr)
    expect("instruction has the five sections and no Notion section",
           all(s in instr for s in ("**Project Timeline:**", "**Task List:**", "**Budget Breakdown:**",
                                    "**Milestones:**", "**Notion Status:**"))
           and "Projects database ID" not in instr)

    os.environ.update({"NOTION_TOKEN": FAKE_TOKEN, "NOTION_PROJECT_DATABASE_ID": FAKE_PROJECTS_DB,
                       "NOTION_TASKS_DATABASE_ID": FAKE_TASKS_DB})
    try:
        agent = pm.create_project_manager_agent()
    finally:
        for k in ("NOTION_TOKEN", "NOTION_PROJECT_DATABASE_ID", "NOTION_TASKS_DATABASE_ID"):
            os.environ.pop(k, None)
    params = agent.tools[0]._inner._connection_params.server_params
    expect("Notion branch: guarded MCP toolset + callbacks",
           isinstance(agent.tools[0], pm.GuardedNotionToolset)
           and agent.after_tool_callback is pm.handle_notion_error
           and agent.on_tool_error_callback is pm.handle_notion_exception and agent.before_agent_callback is not None)
    expect("server started through the stderr-discarding wrapper",
           params.command == "sh" and params.args == ["-c", "exec notion-mcp-server 2>/dev/null"]
           and set(params.env) == {"NOTION_TOKEN", "PATH"})
    ctx = type("C", (), {"state": {pm.NOTION_AVAILABLE_KEY: True}})()
    instr = agent.instruction(ctx)
    expect("Notion instruction: braces rendered, no unfilled placeholders",
           '{"relation": [{"id": "<project-page-id>"}]}' in instr and "{{" not in instr)
    ctx.state[pm.NOTION_AVAILABLE_KEY] = False
    instr = agent.instruction(ctx)
    expect("Notion unavailable -> 'Notion unavailable' status, no Notion operations",
           "Notion unavailable - text timeline only" in instr and "API-<operation>" not in instr)


async def test_guarded_toolset() -> None:
    class Broken:
        async def get_tools(self, readonly_context=None):
            raise RuntimeError(f"spawn failed {BEARER}{FAKE_TOKEN} {FAKE_TASKS_DB}")

        async def close(self):
            pass

    class Empty(Broken):
        async def get_tools(self, readonly_context=None):
            return []

    expect("unavailable server -> no tools, no crash", await pm.GuardedNotionToolset(Broken()).get_tools() == [])
    expect("server with no tools -> no tools", await pm.GuardedNotionToolset(Empty()).get_tools() == [])


def test_check_secrets() -> None:
    import check_secrets

    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d) / "pr.md"
        f.write_text(f"oops {FAKE_TOKEN}\nfine: secret_id, ntn_... placeholder\n")
        hits = check_secrets.scan([f], {})
    expect("check_secrets finds a token-shaped string, ignores placeholders",
           len(hits) == 1 and hits[0].endswith("notion token") and FAKE_TOKEN not in hits[0], hits)


async def main() -> int:
    print("== error hints and cap")
    test_error_hints()
    print("== raised errors")
    test_exception_hint()
    print("== masked logging")
    test_logging_masked()
    print("== agent branches")
    test_branches()
    print("== guarded toolset")
    await test_guarded_toolset()
    print("== check_secrets")
    test_check_secrets()
    print("== leak regression (fake token, dead proxy)")
    test_leak_regression()
    print(f"\n{'ALL PASSED' if not FAILURES else f'{len(FAILURES)} FAILED: {FAILURES}'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
