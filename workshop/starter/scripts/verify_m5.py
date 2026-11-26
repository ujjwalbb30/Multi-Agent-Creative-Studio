"""Milestone 5 verification: the Project Manager's timeline, Notion sync and error recovery.

Input: the APPROVED campaign from M4 (the M2 captions, the critic_strong verdict and the
clean test image described by concept name only - no gs:// URI, so the bucket never reaches
Notion).

Cases (one PM run each, each in its own child process):
  pm_no_notion            Notion variables blanked for that process -> text timeline only
  pm_notion               your Notion workspace -> project + tasks, read back via the API
  pm_notion_forced_error  NOTION_TASKS_DATABASE_ID overridden for that process only with an
                          ID that does not exist -> 404 rewritten into a hint, no crash

For every case the child's full stdout/stderr is captured and checked for the token, a
bearer header and the full database IDs (never printed). All IDs in the evidence are
masked to their last 4 characters. The script never archives Notion pages.

Usage (from workshop/starter/):
  uv run python scripts/verify_m5.py [--only pm_no_notion ...] [--force]
"""

import argparse
import asyncio
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys

STARTER_DIR = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = STARTER_DIR.parents[1]
DEFAULT_OUT = REPO_ROOT / "project-docs" / "evidence" / "m5"
M2_EVIDENCE = REPO_ROOT / "project-docs" / "evidence" / "m2"
M4_EVIDENCE = REPO_ROOT / "project-docs" / "evidence" / "m4"

sys.path.insert(0, str(STARTER_DIR / "agents"))
sys.path.insert(0, str(STARTER_DIR / "scripts"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(STARTER_DIR / ".env")

# A well-formed ID that does not exist; used only by the forced-error case.
MISSING_DB_ID = "00000000-0000-4000-8000-00000000dead"
NOTION_VARS = ("NOTION_TOKEN", "NOTION_PROJECT_DATABASE_ID", "NOTION_TASKS_DATABASE_ID")
SECTIONS = ["**Project Timeline:**", "**Task List:**", "**Budget Breakdown:**", "**Milestones:**",
            "**Notion Status:**"]
PHASES = ["Strategy", "Creation", "Review", "Launch"]
CASES = {
    "pm_no_notion": "No Notion configured: text timeline only",
    "pm_notion": "Notion configured: project and tasks synced",
    "pm_notion_2": "Repeat of pm_notion",
    "pm_notion_forced_error": "Forced Notion error (missing Tasks database): hint, no crash",
}
UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}\b")
DATE_RE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")


# ---------------------------------------------------------------- masking


def secret_values() -> dict[str, str]:
    vals = {}
    for k in ("NOTION_TOKEN", "NOTION_PROJECT_DATABASE_ID", "NOTION_TASKS_DATABASE_ID",
              "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_PROJECT_NUMBER", "GCS_IMAGES_BUCKET"):
        v = (os.environ.get(k) or "").strip()
        if len(v) >= 6:
            vals[k] = v
    return vals


def mask(text: str) -> str:
    """Masks every UUID-shaped ID to its last 4 characters and removes configured secrets."""
    from project_manager.agent import mask_text

    for name, v in secret_values().items():
        text = text.replace(v, f"<{name}>").replace(v.replace("-", ""), f"<{name}>")
    text = re.sub(r"https://(www\.)?notion\.so/\S+", "<notion-url>", text)
    return mask_text(text)


def leak_findings(output: str) -> list[str]:
    """Names (never values) of secrets found in captured output."""
    found = [f"{name} (value)" for name, v in secret_values().items()
             if name.startswith("NOTION") and (v in output or v.replace("-", "") in output)]
    if re.search("ntn" + r"_[A-Za-z0-9]{20,}", output):
        found.append("token-shaped text")
    if ("Bear" + "er ") in output:
        found.append("bearer header")
    return found


# ---------------------------------------------------------------- child: one PM run


def approved_campaign() -> str:
    captions = json.loads((M2_EVIDENCE / "sequence_b.json").read_text())["final_text"]
    verdict = json.loads((M4_EVIDENCE / "critic_strong.json").read_text())["final_text"]
    clean = json.loads((M4_EVIDENCE / "clean_image.json").read_text())
    return (
        "Plan this APPROVED Instagram campaign.\n\n"
        "Brand: Ember & Oak, a small-batch coffee roastery in Austin, Texas. Audience: local remote workers.\n\n"
        f"APPROVED CAPTIONS:\n{captions}\n\n"
        "APPROVED VISUALS:\n"
        "- clean_beans_still_life: overhead still life of a ceramic bowl of roasted coffee beans and a stone "
        f"cup of black coffee on warm oak, soft window light (square {clean['aspect_ratio']}).\n\n"
        f"CRITIC VERDICT:\n{verdict}\n"
    )


async def child(case: str, result_path: pathlib.Path) -> None:
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    from project_manager.agent import create_project_manager_agent

    agent = create_project_manager_agent()
    sessions = InMemorySessionService()
    runner = Runner(app_name="verify_m5", agent=agent, session_service=sessions)
    session = await sessions.create_session(app_name="verify_m5", user_id="verifier")
    texts, calls, responses = [], [], []
    try:
        async for event in runner.run_async(user_id="verifier", session_id=session.id,
                                            new_message=types.Content(role="user", parts=[types.Part(text=approved_campaign())])):
            for c in event.get_function_calls():
                calls.append({"name": c.name, "args": mask(json.dumps(dict(c.args or {})))[:600]})
            for r in event.get_function_responses():
                responses.append({"name": r.name, "response": mask(json.dumps(r.response, default=str))[:600]})
            if event.is_final_response() and event.content and event.content.parts:
                texts.extend(p.text for p in event.content.parts if p.text and not p.thought)
    finally:
        for tool in agent.tools or []:
            if hasattr(tool, "close"):
                await tool.close()
    final = await sessions.get_session(app_name="verify_m5", user_id="verifier", session_id=session.id)
    notion_available = final.state.get("pm_notion_available")
    repairs = [mask(r) for k, v in final.state.items() if k.startswith("pm_notion_repair:") and v for r in v]
    result = {"final_text": mask("\n".join(texts).strip()), "tool_calls": calls, "tool_responses": responses,
              "notion_available": notion_available, "tools_attached": bool(agent.tools), "repairs": repairs}
    result_path.write_text(json.dumps(result, indent=2))
    print(f"CHILD_DONE {case}: {len(calls)} tool calls")


# ---------------------------------------------------------------- Notion read-back (read-only)


def notion_query(db_env: str, since_iso: str) -> list[dict]:
    import httpx

    headers = {"Authorization": "Bear" + "er " + os.environ["NOTION_TOKEN"], "Notion-Version": "2022-06-28"}
    body = {"filter": {"timestamp": "created_time", "created_time": {"on_or_after": since_iso}}, "page_size": 100}
    try:
        r = httpx.post(f"https://api.notion.com/v1/databases/{os.environ[db_env]}/query",
                       headers=headers, json=body, timeout=30)
        r.raise_for_status()
    except Exception as e:
        # httpx messages include the request URL, which contains the full database ID.
        raise RuntimeError(f"Notion read-back failed for {db_env}: {mask(str(e))}") from None
    return r.json()["results"]


def title_of(page: dict) -> str:
    for prop in page["properties"].values():
        if prop["type"] == "title":
            return "".join(t.get("plain_text", "") for t in prop["title"])
    return ""


def existing_ids(since_iso: str) -> set[str]:
    """Pages already in the window before a run (Notion's created_time is minute-precise)."""
    return {p["id"] for db in ("NOTION_PROJECT_DATABASE_ID", "NOTION_TASKS_DATABASE_ID")
            for p in notion_query(db, since_iso)}


def read_back(since_iso: str, before: set[str]) -> dict:
    """Pages created by this run: in the window now, and not there before the run."""
    projects = [p for p in notion_query("NOTION_PROJECT_DATABASE_ID", since_iso) if p["id"] not in before]
    tasks = [t for t in notion_query("NOTION_TASKS_DATABASE_ID", since_iso) if t["id"] not in before]
    project_ids = {p["id"] for p in projects}
    linked = 0
    for t in tasks:
        for prop in t["properties"].values():
            if prop["type"] == "relation" and any(r["id"] in project_ids for r in prop["relation"]):
                linked += 1
                break
    return {"projects": [{"title": title_of(p), "id": f"…{p['id'][-4:]}"} for p in projects],
            "tasks": [{"title": title_of(t), "id": f"…{t['id'][-4:]}"} for t in tasks],
            "tasks_linked_to_new_project": linked}


# ---------------------------------------------------------------- checks


def common_checks(text: str) -> list[dict]:
    today = datetime.date.today()
    out = [{"check": "final response is not empty", "ok": bool(text), "detail": f"{len(text)} chars"}]
    pos = [text.find(s) for s in SECTIONS]
    out.append({"check": "five sections present, in order", "ok": all(p >= 0 for p in pos) and pos == sorted(pos),
                "detail": "" if all(p >= 0 for p in pos) else f"missing {[s for s, p in zip(SECTIONS, pos) if p < 0]}"})
    timeline = text[pos[0]:pos[1]] if pos[0] >= 0 and pos[1] > pos[0] else ""
    ph = [timeline.find(p) for p in PHASES]
    out.append({"check": "timeline has Strategy, Creation, Review, Launch in order",
                "ok": all(p >= 0 for p in ph) and ph == sorted(ph)})
    dates = [datetime.date.fromisoformat(d) for d in DATE_RE.findall(text)]
    out.append({"check": "dates start from today (none in the past, first within 7 days)",
                "ok": bool(dates) and min(dates) >= today and (min(dates) - today).days <= 7,
                "detail": f"{len(dates)} dates, {min(dates) if dates else '-'} to {max(dates) if dates else '-'}"})
    task_list = text[pos[1]:pos[2]] if pos[1] >= 0 and pos[2] > pos[1] else ""
    rows = [l for l in task_list.splitlines() if l.strip().startswith("|") and "---" not in l]
    header_ok = bool(rows) and all(h in rows[0] for h in ("Task", "Owner", "Deadline", "Status"))
    out.append({"check": "task table has Task | Owner | Deadline | Status and 6+ tasks",
                "ok": header_ok and len(rows) - 1 >= 6, "detail": f"{max(len(rows) - 1, 0)} tasks"})
    captions = ["Productivity Pivot", "Swavory", "Story of the Batch"]
    missing = [c for c in captions if c.lower() not in task_list.lower()]
    out.append({"check": "tasks name every approved caption", "ok": not missing,
                "detail": f"missing {missing}" if missing else ""})
    out.append({"check": "plan references the approved visual", "ok": "clean_beans_still_life" in text
                or "still life" in text.lower(), "detail": ""})
    budget = text[pos[2]:pos[3]] if pos[2] >= 0 and pos[3] > pos[2] else ""
    out.append({"check": "budget has a total", "ok": "total" in budget.lower()})
    out.append({"check": "response contains no full IDs or Notion URLs",
                "ok": not UUID_RE.search(text) and "notion.so/" not in text})
    return out


def notion_status(text: str) -> str:
    i = text.find("**Notion Status:**")
    return text[i + len("**Notion Status:**"):].strip()[:400] if i >= 0 else ""


def case_checks(case: str, r: dict, back: dict | None, leaks: list[str], exit_code: int) -> list[dict]:
    text = r.get("final_text", "")
    out = [{"check": "run finished without crashing", "ok": exit_code == 0, "detail": f"exit {exit_code}"},
           {"check": "captured stdout/stderr contains no token, bearer header or full database ID",
            "ok": not leaks, "detail": f"found: {leaks}" if leaks else ""}]
    out += common_checks(text)
    status = notion_status(text)
    names = [c["name"] for c in r.get("tool_calls", [])]
    if case == "pm_no_notion":
        out.append({"check": "no tools attached, no tool calls", "ok": not r.get("tools_attached") and not names})
        out.append({"check": "Notion Status says not configured", "ok": "not configured" in status.lower(),
                    "detail": status[:120]})
    elif case in ("pm_notion", "pm_notion_2"):
        out.append({"check": "run-start check found the Notion tools", "ok": r.get("notion_available") is True})
        out.append({"check": "schema discovered before writing (API-retrieve-a-database first)",
                    "ok": "API-retrieve-a-database" in names and "API-post-page" in names
                    and names.index("API-retrieve-a-database") < names.index("API-post-page"),
                    "detail": f"{len(names)} calls: {sorted(set(names))}"})
        out.append({"check": "Notion read-back: one project page created", "ok": back and len(back["projects"]) == 1,
                    "detail": f"{[p['title'] for p in back['projects']] if back else '-'}"})
        out.append({"check": "Notion read-back: tasks created and linked to the project",
                    "ok": back and len(back["tasks"]) >= 6 and back["tasks_linked_to_new_project"] == len(back["tasks"]),
                    "detail": f"{len(back['tasks']) if back else 0} tasks, {back['tasks_linked_to_new_project'] if back else 0} linked"})
        titles = [p["title"] for p in back["projects"]] if back else []
        out.append({"check": "Notion Status reports the actual creation (read-back finds the project; "
                             "never 'not configured')",
                    "ok": bool(titles) and "not configured" not in status.lower()
                    and (any(t and t.lower() in status.lower() for t in titles) or "created" in status.lower()),
                    "detail": status[:160]})
    else:
        hints = [x["response"] for x in r.get("tool_responses", []) if "Notion 404 (object_not_found)" in x["response"]]
        out.append({"check": "a Notion 404 was rewritten into the recovery hint",
                    "ok": bool(hints) and any("Connections" in h and "database_id" in h for h in hints),
                    "detail": f"{len(hints)} rewritten 404s"})
        out.append({"check": "no tasks written to the real Tasks database", "ok": back is not None and not back["tasks"],
                    "detail": f"{len(back['tasks']) if back else '-'} tasks"})
        out.append({"check": "Notion Status reports the failure (never 'not configured')",
                    "ok": "not configured" not in status.lower()
                    and any(w in status.lower() for w in ("fail", "error", "could not", "unable", "not found")),
                    "detail": status[:160]})
    return out


# ---------------------------------------------------------------- evidence


def git_commit() -> str:
    def git(*args):
        return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True).stdout.strip()
    dirty = git("status", "--porcelain", "--", "workshop/starter/agents", "workshop/starter/scripts")
    return git("rev-parse", "--short", "HEAD") + (" (uncommitted changes in agents/ or scripts/)" if dirty else "")


def write_case(out_dir: pathlib.Path, case: str, data: dict) -> None:
    (out_dir / f"{case}.json").write_text(mask(json.dumps(data, indent=2, default=str)))
    lines = [f"# {CASES[case]}", "", f"Case `{case}`, run {data['ran_at']}, model `{data['model']}`, commit `{data['commit']}`.",
             "", "## Checks", ""]
    lines += [f"- {'PASS' if c['ok'] else 'FAIL'}: {c['check']}" + (f" ({c['detail']})" if c.get("detail") else "")
              for c in data["checks"]]
    if data.get("read_back"):
        b = data["read_back"]
        lines += ["", "## Notion read-back (pages created during this run; IDs masked)", "",
                  f"- Projects: {[p['title'] + ' ' + p['id'] for p in b['projects']]}",
                  f"- Tasks ({len(b['tasks'])}, {b['tasks_linked_to_new_project']} linked to the new project):"]
        lines += [f"  - {t['title']} {t['id']}" for t in b["tasks"]]
    lines += ["", f"## Argument repairs ({len(data.get('repairs') or [])})", ""]
    lines += [f"- {r}" for r in data.get("repairs") or []] or ["- none"]
    if data.get("tool_calls"):
        lines += ["", "## Tool calls (arguments masked and truncated)", ""]
        lines += [f"- `{c['name']}` {c['args'][:200]}" for c in data["tool_calls"]]
    errors = [x for x in data.get("tool_responses", []) if "Notion " in x["response"] and "on API-" in x["response"]]
    if errors:
        lines += ["", "## Rewritten Notion errors (what the model saw)", ""]
        lines += [f"- `{x['name']}`: {x['response'][:400]}" for x in errors]
    lines += ["", "## Final response", "", data.get("final_text") or "_(empty)_", ""]
    (out_dir / f"{case}.md").write_text(mask("\n".join(lines)))


def write_summary(out_dir: pathlib.Path, log: list) -> None:
    lines = ["# Milestone 5 verification summary", "",
             "Generated by `workshop/starter/scripts/verify_m5.py`. Every ID is masked to its last 4 characters.", "",
             "| Case | Checks passed | Tool calls | Repairs | Notion read-back | Leak check | Commit |",
             "|---|---|---|---|---|---|---|"]
    for case, title in CASES.items():
        p = out_dir / f"{case}.json"
        if not p.exists():
            lines.append(f"| [{case}]({case}.md) | not run | | | | | |")
            continue
        d = json.loads(p.read_text())
        ok = sum(c["ok"] for c in d["checks"])
        b = d.get("read_back")
        back = f"{len(b['projects'])} project, {len(b['tasks'])} tasks" if b else "-"
        leak = "clean" if not d.get("leaks") else "LEAK"
        lines.append(f"| [{case}]({case}.md): {title} | {ok}/{len(d['checks'])} | {len(d.get('tool_calls', []))} | "
                     f"{len(d.get('repairs') or [])} | {back} | {leak} | `{d['commit']}` |")
    used = sum(1 for e in log if e.get("counted", True))
    lines += ["", f"PM runs used: {used} of 8. Full history: [run_log.json](run_log.json).", ""]
    notes = out_dir / "NOTES.md"
    if notes.exists():
        lines += [notes.read_text().strip(), ""]
    (out_dir / "README.md").write_text("\n".join(lines))


def run_case(case: str, out_dir: pathlib.Path) -> dict:
    env = dict(os.environ)
    if case == "pm_no_notion":
        for k in NOTION_VARS:
            env[k] = ""  # blank wins over .env (load_dotenv does not override set variables)
    elif case == "pm_notion_forced_error":
        env["NOTION_TASKS_DATABASE_ID"] = MISSING_DB_ID  # this child process only; .env is untouched
    result_path = out_dir / f".{case}.raw.json"
    since = datetime.datetime.now(datetime.timezone.utc).replace(second=0, microsecond=0)
    since -= datetime.timedelta(minutes=1)
    before = existing_ids(since.isoformat()) if case != "pm_no_notion" else set()
    proc = subprocess.run([sys.executable, "-W", "ignore", str(pathlib.Path(__file__)), "--child", case,
                           "--result", str(result_path)], env=env, cwd=STARTER_DIR,
                          capture_output=True, text=True, timeout=1800)
    output = proc.stdout + proc.stderr
    leaks = leak_findings(output)
    r = json.loads(result_path.read_text()) if result_path.exists() else {}
    result_path.unlink(missing_ok=True)
    back = read_back(since.isoformat(), before) if case != "pm_no_notion" else None
    tail = mask("\n".join(output.strip().splitlines()[-5:])) if proc.returncode else ""
    return {**r, "read_back": back, "leaks": leaks, "exit_code": proc.returncode, "error_tail": tail,
            "since": since.isoformat()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", nargs="+", choices=list(CASES))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--budget", type=int, default=8)
    parser.add_argument("--child", choices=list(CASES), help=argparse.SUPPRESS)
    parser.add_argument("--result", type=pathlib.Path, help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.child:
        asyncio.run(child(args.child, args.result))
        return 0

    out_dir = DEFAULT_OUT
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "run_log.json"
    log = json.loads(log_path.read_text()) if log_path.exists() else []
    for case in CASES:
        if args.only and case not in args.only:
            continue
        if (out_dir / f"{case}.json").exists() and not args.force:
            print(f"[skip] {case}: cached")
            continue
        if sum(1 for e in log if e.get("counted", True)) >= args.budget:
            print(f"[stop] budget of {args.budget} PM runs reached; not running {case}")
            break
        log.append({"case": case, "at": datetime.datetime.now().isoformat(timespec="seconds")})
        log_path.write_text(json.dumps(log, indent=2))
        print(f"[run ] {case} ...", flush=True)
        r = run_case(case, out_dir)
        from project_manager.agent import GEMINI_MODEL
        r |= {"checks": case_checks(case, r, r["read_back"], r["leaks"], r["exit_code"]),
              "ran_at": log[-1]["at"], "model": GEMINI_MODEL, "commit": git_commit()}
        write_case(out_dir, case, r)
        failed = [c["check"] for c in r["checks"] if not c["ok"]]
        print(f"[done] {case}: {len(r['checks']) - len(failed)}/{len(r['checks'])} checks passed"
              + (f"; FAILED: {failed}" if failed else ""))
        if r["exit_code"]:
            print(f"[stop] child exited {r['exit_code']}; last lines (masked):\n{r['error_tail']}")
            break

    write_summary(out_dir, log)
    import check_secrets
    files = sorted(p for p in out_dir.rglob("*") if p.is_file())
    hits = check_secrets.scan(files, check_secrets.load_values())
    print(f"Secret scan of evidence ({len(files)} files): " + ("OK" if not hits else f"FAIL {hits}"))
    print(f"PM runs used: {sum(1 for e in log if e.get('counted', True))}/{args.budget}. Evidence: {out_dir}")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
