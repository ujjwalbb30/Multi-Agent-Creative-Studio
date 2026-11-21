"""Milestone 2 verification: Brand Strategist and Copywriter, alone and in sequence.

Runs each agent in-process with an ADK Runner (no deployment), checks the output
shape, and writes redacted evidence to project-docs/evidence/m2/.

Cases (one paid agent run each):
  strategist_a        research brief A; four sections, year in every search query
  strategist_b        research brief B; same checks, second brief
  strategist_caption  "write me a caption" brief; must stay research-only
  copywriter_alone    brief A with no research; 3 captions, Skill tools called
  sequence            brief A with strategist_a's output in the conversation history

Each finished case is cached in the evidence folder and skipped on later runs
(use --force to rerun). Every paid run is logged in run_log.json, and the script
refuses to go past --budget runs in total.

Usage (from workshop/starter/):
  uv run python scripts/verify_m2.py                 # run all cases not yet cached
  uv run python scripts/verify_m2.py --only sequence # one case
"""

import argparse
import asyncio
import datetime
import json
import os
import pathlib
import re
import sys
import uuid

STARTER_DIR = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = STARTER_DIR.parents[1]
DEFAULT_OUT = REPO_ROOT / "project-docs" / "evidence" / "m2"

sys.path.insert(0, str(STARTER_DIR / "agents"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(STARTER_DIR / ".env")

from google.adk.events import Event  # noqa: E402
from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions import InMemorySessionService  # noqa: E402
from google.genai import types  # noqa: E402

from brand_strategist.agent import root_agent as strategist_agent  # noqa: E402
from copywriter.agent import root_agent as copywriter_agent  # noqa: E402

YEAR = str(datetime.date.today().year)

BRIEF_A = (
    "Campaign brief: AquaLeaf is a new reusable, plant-based water bottle launching on "
    "Instagram to US college students (Gen Z). Goal: build awareness for the launch."
)
BRIEF_B = (
    "Campaign brief: Ember & Oak is a small-batch coffee roastery in Austin, Texas that "
    "wants to grow its Instagram following among local remote workers."
)
CAPTION_REQUEST = (
    "Write me an Instagram caption for AquaLeaf's new reusable water bottle. "
    "Just the caption with hashtags, no research needed."
)
COPY_REQUEST = f"Write the Instagram captions for this campaign. {BRIEF_A}"

SECTION_LABELS = [
    "**Audience Insights:**",
    "**Competitive Analysis:**",
    "**Trending Topics:**",
    "**Key Strategic Insights:**",
]
SKILL_TOOLS = {"list_skills", "load_skill", "load_skill_resource"}
HASHTAG_RE = re.compile(r"(?<![\w&/])#[A-Za-z][\w]*")
CAPTION_HEADER_RE = re.compile(r"^\*\*Caption\s+(\d+):\s*(.*?)\*\*\s*$", re.MULTILINE)

STOPWORDS = set(
    """about above after again against their there these those which while would could
    should being other every under within without where whose because between through
    during before brand brands campaign instagram users content media social post posts
    really often still using based students college water bottle reusable launch
    awareness""".split()
)


# ---------------------------------------------------------------- helpers


def redact(text: str) -> str:
    for key, tag in (
        ("GOOGLE_CLOUD_PROJECT", "<PROJECT_ID>"),
        ("GOOGLE_CLOUD_PROJECT_NUMBER", "<PROJECT_NUMBER>"),
        ("GCS_IMAGES_BUCKET", "<BUCKET>"),
    ):
        value = os.getenv(key)
        if value:
            text = text.replace(value, tag)
    return re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "<EMAIL>", text)


def check(name: str, ok: bool, detail: str = "") -> dict:
    return {"check": name, "ok": bool(ok), "detail": detail}


def hashtags(text: str) -> list[str]:
    return HASHTAG_RE.findall(text)


def split_captions(text: str) -> list[dict]:
    headers = list(CAPTION_HEADER_RE.finditer(text))
    captions = []
    for i, m in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        body = text[m.end():end]
        captions.append(
            {
                "number": int(m.group(1)),
                "title": m.group(2).strip(),
                "body": body,
                "hashtags": hashtags(body),
                "has_cta": bool(re.search(r"^\s*\**CTA:?\**:?", body, re.MULTILINE)),
            }
        )
    return captions


def research_terms(strategist_text: str, brief: str) -> set[str]:
    """Distinctive words from the research that are not already in the brief."""
    brief_words = set(re.findall(r"[a-z]{5,}", brief.lower()))
    words = set(re.findall(r"[a-z]{6,}", strategist_text.lower()))
    return words - brief_words - STOPWORDS


# ---------------------------------------------------------------- running


async def run_agent(agent, prompt: str, history_text: str | None = None) -> dict:
    """Runs one agent turn and collects final text, tool calls and search queries."""
    service = InMemorySessionService()
    runner = Runner(app_name="verify_m2", agent=agent, session_service=service)
    session = await service.create_session(app_name="verify_m2", user_id="verifier")

    if history_text:
        # Put the Strategist's research in the conversation the way the orchestrator
        # would: as an earlier event authored by another agent.
        await service.append_event(
            session,
            Event(
                invocation_id=f"e-{uuid.uuid4()}",
                author="brand_strategist",
                content=types.Content(role="model", parts=[types.Part(text=history_text)]),
            ),
        )

    tool_calls, queries, texts = [], [], []
    async for event in runner.run_async(
        user_id="verifier",
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=prompt)]),
    ):
        for call in event.get_function_calls():
            tool_calls.append({"name": call.name, "args": dict(call.args or {})})
        gm = event.grounding_metadata
        if gm and gm.web_search_queries:
            queries.extend(q for q in gm.web_search_queries if q not in queries)
        if event.is_final_response() and event.content and event.content.parts:
            texts.extend(p.text for p in event.content.parts if p.text and not p.thought)

    return {
        "prompt": prompt,
        "final_text": "\n".join(texts).strip(),
        "tool_calls": tool_calls,
        "web_search_queries": queries,
    }


# ---------------------------------------------------------------- checks


def strategist_checks(result: dict) -> list[dict]:
    text, queries = result["final_text"], result["web_search_queries"]
    positions = [text.find(label) for label in SECTION_LABELS]
    missing = [l for l, p in zip(SECTION_LABELS, positions) if p < 0]
    no_year = [q for q in queries if YEAR not in q]
    tags = hashtags(text)
    return [
        check("all four section labels present", not missing, f"missing: {missing}" if missing else ""),
        check("sections in the required order", not missing and positions == sorted(positions)),
        check("used google_search (web_search_queries recorded)", bool(queries), f"{len(queries)} queries"),
        check(f"every search query includes {YEAR}", bool(queries) and not no_year,
              f"without year: {no_year}" if no_year else ""),
        check("no captions in output", not CAPTION_HEADER_RE.search(text) and "CTA:" not in text),
        check("no hashtags in output", not tags, f"found: {tags}" if tags else ""),
    ]


def copywriter_checks(result: dict) -> list[dict]:
    captions = split_captions(result["final_text"])
    called = {c["name"] for c in result["tool_calls"]}
    resources = [c["args"].get("path") or c["args"].get("resource_path") or c["args"]
                 for c in result["tool_calls"] if c["name"] == "load_skill_resource"]
    titles = [c["title"] for c in captions]
    out = [
        check("loaded the instagram-copywriting Skill (load_skill called)", "load_skill" in called,
              f"tools called: {sorted(called)}"),
        check("read a Skill resource (load_skill_resource called)", "load_skill_resource" in called,
              f"resources: {resources}"),
        check("exactly 3 captions", len(captions) == 3, f"found {len(captions)}"),
        check("captions have distinct theme titles", len(set(titles)) == len(titles), f"titles: {titles}"),
    ]
    for c in captions:
        n = len(c["hashtags"])
        out.append(check(f"caption {c['number']}: 5-10 hashtags", 5 <= n <= 10, f"{n} hashtags"))
        out.append(check(f"caption {c['number']}: has a CTA line", c["has_cta"]))
    return out


def sequence_checks(result: dict, research: dict, alone: dict | None) -> list[dict]:
    terms = research_terms(research["final_text"], BRIEF_A)
    seq_text = result["final_text"].lower()
    used = sorted(t for t in terms if t in seq_text)
    out = copywriter_checks(result)
    detail = f"{len(used)} research terms reused: {used[:25]}"
    if alone:
        alone_text = alone["final_text"].lower()
        only_seq = sorted(t for t in used if t not in alone_text)
        alone_used = sorted(t for t in terms if t in alone_text)
        detail += (f" | copywriter_alone reused {len(alone_used)} | "
                   f"only in the sequence run: {only_seq[:25]}")
        out.append(check("sequence reuses more research terms than copywriter_alone",
                         len(used) > len(alone_used), detail))
    else:
        out.append(check("captions reuse research terms", len(used) >= 3, detail))
    return out


# ---------------------------------------------------------------- evidence


def write_case(out_dir: pathlib.Path, case: str, title: str, result: dict, checks: list[dict]) -> None:
    (out_dir / f"{case}.json").write_text(redact(json.dumps({**result, "checks": checks}, indent=2)))
    lines = [f"# {title}", "", f"Case `{case}`, run {result['ran_at']}, model `{result['model']}`.", "",
             "## Checks", ""]
    lines += [f"- {'PASS' if c['ok'] else 'FAIL'}: {c['check']}" + (f" ({c['detail']})" if c["detail"] else "")
              for c in checks]
    lines += ["", "## Prompt", "", "```text", result["prompt"], "```", ""]
    if result["web_search_queries"]:
        lines += ["## Google Search queries (from grounding metadata)", ""]
        lines += [f"- `{q}`" for q in result["web_search_queries"]] + [""]
    if result["tool_calls"]:
        lines += ["## Tool calls", ""]
        lines += [f"- `{c['name']}` {json.dumps(c['args'])}" for c in result["tool_calls"]] + [""]
    lines += ["## Final response", "", result["final_text"], ""]
    (out_dir / f"{case}.md").write_text(redact("\n".join(lines)))


def load_case(out_dir: pathlib.Path, case: str) -> dict | None:
    path = out_dir / f"{case}.json"
    return json.loads(path.read_text()) if path.exists() else None


def write_summary(out_dir: pathlib.Path, cases: dict) -> None:
    lines = ["# Milestone 2 verification summary", "",
             "Generated by `workshop/starter/scripts/verify_m2.py`. Project identifiers redacted.", "",
             "| Case | Checks passed | Search queries | Tool calls |", "|---|---|---|---|"]
    for case, title in cases.items():
        data = load_case(out_dir, case)
        if not data:
            lines.append(f"| [{case}]({case}.md) | not run | | |")
            continue
        ok = sum(c["ok"] for c in data["checks"])
        tools = ", ".join(sorted({c["name"] for c in data["tool_calls"]})) or "-"
        lines.append(f"| [{case}]({case}.md): {title} | {ok}/{len(data['checks'])} | "
                     f"{len(data['web_search_queries'])} | {tools} |")
    log = json.loads((out_dir / "run_log.json").read_text()) if (out_dir / "run_log.json").exists() else []
    lines += ["", f"Paid agent runs used: {len(log)}", ""]
    (out_dir / "README.md").write_text("\n".join(lines))


# ---------------------------------------------------------------- main

CASES = {
    "strategist_a": "Strategist, brief A (AquaLeaf)",
    "strategist_b": "Strategist, brief B (Ember & Oak)",
    "strategist_caption": "Strategist asked for a caption (must stay research-only)",
    "copywriter_alone": "Copywriter alone, brief A, no research",
    "sequence": "Copywriter after Strategist (brief A research in history)",
}


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", nargs="+", choices=list(CASES), help="cases to run")
    parser.add_argument("--force", action="store_true", help="rerun cases already cached")
    parser.add_argument("--budget", type=int, default=10, help="max paid agent runs in total")
    parser.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "run_log.json"
    log = json.loads(log_path.read_text()) if log_path.exists() else []

    selected = args.only or list(CASES)
    if "sequence" in selected and not load_case(out_dir, "strategist_a") and "strategist_a" not in selected:
        selected.insert(0, "strategist_a")

    for case in CASES:
        if case not in selected:
            continue
        if load_case(out_dir, case) and not args.force:
            print(f"[skip] {case}: cached (use --force to rerun)")
            continue
        if len(log) >= args.budget:
            print(f"[stop] budget of {args.budget} paid runs reached; not running {case}")
            break

        if case.startswith("strategist"):
            prompt = {"strategist_a": BRIEF_A, "strategist_b": BRIEF_B,
                      "strategist_caption": CAPTION_REQUEST}[case]
            print(f"[run ] {case} ...", flush=True)
            result = await run_agent(strategist_agent, prompt)
            model = strategist_agent.model
        else:
            research = load_case(out_dir, "strategist_a") if case == "sequence" else None
            print(f"[run ] {case} ...", flush=True)
            result = await run_agent(copywriter_agent, COPY_REQUEST,
                                     history_text=research["final_text"] if research else None)
            model = copywriter_agent.model

        log.append({"case": case, "at": datetime.datetime.now().isoformat(timespec="seconds")})
        log_path.write_text(json.dumps(log, indent=2))
        result |= {"ran_at": log[-1]["at"], "model": model}

        if case.startswith("strategist"):
            checks = strategist_checks(result)
        elif case == "copywriter_alone":
            checks = copywriter_checks(result)
        else:
            checks = sequence_checks(result, research, load_case(out_dir, "copywriter_alone"))
        write_case(out_dir, case, CASES[case], result, checks)
        failed = [c["check"] for c in checks if not c["ok"]]
        print(f"[done] {case}: {len(checks) - len(failed)}/{len(checks)} checks passed"
              + (f"; FAILED: {failed}" if failed else ""))

    write_summary(out_dir, CASES)
    print(f"Paid runs used: {len(log)}/{args.budget}. Evidence: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
