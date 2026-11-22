"""Milestone 4 verification: the Critic reviews real images and returns a strict verdict.

Reuses the M2 captions and the M3 images already in the bucket (no image generation).

Cases (one Critic run each):
  critic_strong                  M2 captions + the cleanest M3 image        -> expect APPROVED
  critic_weak                    weak captions + off-brand image with logo  -> expect NEEDS_REVISION,
                                                                               logo flagged
  critic_mixed                   M2 captions + 3 images of mixed quality    -> VISUALS = lowest score
  critic_no_images               M2 captions only                           -> VISUALS NOT_REVIEWED
  critic_weak_posts_good_visuals weak captions + the cleanest M3 image      -> POSTS NEEDS_REVISION
  critic_strong_2, critic_weak_2 repeats, to show the format holds

Evidence goes to project-docs/evidence/m4/ with the bucket, project ID, project
number and emails redacted. Budget: --budget Critic runs (default 10); runs
rejected with HTTP 403 are logged but not counted.

Usage (from workshop/starter/):
  uv run python scripts/verify_m4.py
  uv run python scripts/verify_m4.py --only critic_strong
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
DEFAULT_OUT = REPO_ROOT / "project-docs" / "evidence" / "m4"
M2_EVIDENCE = REPO_ROOT / "project-docs" / "evidence" / "m2"
M3_EVIDENCE = REPO_ROOT / "project-docs" / "evidence" / "m3"

sys.path.insert(0, str(STARTER_DIR / "agents"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(STARTER_DIR / ".env")

from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions import InMemorySessionService  # noqa: E402
from google.genai import types  # noqa: E402

from critic.agent import GUARD_KEY, root_agent as critic_agent  # noqa: E402
from critic.verdict import VerdictParseError, enforce, parse_verdict  # noqa: E402

BUCKET = os.environ["GCS_IMAGES_BUCKET"]
LOGO_RE = re.compile(r"logo|trademark|brand(ed|ing)?\b|hydro ?flask", re.IGNORECASE)

WEAK_CAPTIONS = """**Caption 1: Coffe**
we have coffe. its good i guess. come buy some or dont lol
.
#coffee #love #instagood #photooftheday #happy #food
CTA: idk

**Caption 2: Products**
Our company sells many products including coffee beans and other items. Our products are available
for purchase. We have been in business for some time and we think you might like our products.
.
#business #products #sale
CTA: Buy now

**Caption 3: Tea Time**
Nothing beats a relaxing cup of green tea on a rainy London afternoon!!! Tea lovers unite!!!
.
#tea #greentea #london #teatime #relax #cozy #rain #uk #teaaddict #morning #vibes #chill
CTA: Visit our London tea shop"""

BRIEF = ("Campaign: Ember & Oak, a small-batch coffee roastery in Austin, Texas, growing its Instagram "
         "following among local remote workers.")


def m3_uri(name_fragment: str) -> str:
    """Real gs:// URI of an M3 image (the evidence stores it with the bucket redacted)."""
    for case in ("designer_sequence", "tool_portrait", "tool_square"):
        data = json.loads((M3_EVIDENCE / f"{case}.json").read_text())
        for img in data["images"]:
            if img.get("gcs_uri") and name_fragment in img["gcs_uri"]:
                return img["gcs_uri"].replace("<BUCKET>", BUCKET)
    raise KeyError(name_fragment)


def strong_captions() -> str:
    return json.loads((M2_EVIDENCE / "sequence_b.json").read_text())["final_text"]


IMG_CLEAN = ("caption3_story_of_the_batch", "caption3_story_of_the_batch")
IMG_GARBLED_TEXT = ("caption2_swavory_ritual", "caption2_swavory_ritual")
IMG_LOGO_OFF_BRAND = ("tool_portrait", "tool_portrait")

CASES = {
    "critic_strong": ("M2 captions + the cleanest M3 image", "strong", [IMG_CLEAN]),
    "critic_weak": ("Weak captions + off-brand image with a real logo", "weak", [IMG_LOGO_OFF_BRAND]),
    "critic_mixed": ("M2 captions + 3 images of mixed quality", "strong", [IMG_CLEAN, IMG_GARBLED_TEXT, IMG_LOGO_OFF_BRAND]),
    "critic_no_images": ("M2 captions, no images", "strong", []),
    "critic_weak_posts_good_visuals": ("Weak captions + the cleanest M3 image", "weak", [IMG_CLEAN]),
    "critic_strong_2": ("Repeat of critic_strong", "strong", [IMG_CLEAN]),
    "critic_weak_2": ("Repeat of critic_weak", "weak", [IMG_LOGO_OFF_BRAND]),
}


# ---------------------------------------------------------------- helpers


def redact(text: str) -> str:
    for key, tag in (("GCS_IMAGES_BUCKET", "<BUCKET>"), ("GOOGLE_CLOUD_PROJECT_NUMBER", "<PROJECT_NUMBER>"),
                     ("GOOGLE_CLOUD_PROJECT", "<PROJECT_ID>")):
        value = os.getenv(key)
        if value:
            text = text.replace(value, tag)
    return re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "<EMAIL>", text)


def check(name: str, ok: bool, detail: str = "") -> dict:
    return {"check": name, "ok": bool(ok), "detail": detail}


def git_commit() -> str:
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True).stdout.strip()
    dirty = git("status", "--porcelain", "--", "workshop/starter/agents", "workshop/starter/scripts")
    return git("rev-parse", "--short", "HEAD") + (" (uncommitted changes in agents/ or scripts/)" if dirty else "")


def build_message(captions: str, images: list[tuple[str, str]]) -> tuple[str, list[str]]:
    uris = [m3_uri(fragment) for _, fragment in images]
    text = f"Review this Instagram campaign.\n\n{BRIEF}\n\nCAPTIONS:\n{captions}\n"
    if images:
        text += "\nIMAGES (generated by the Designer):\n" + "".join(
            f"- {concept}: {uri}\n" for (concept, _), uri in zip(images, uris))
    return text, uris


# ---------------------------------------------------------------- running


async def run_critic(message: str) -> dict:
    sessions = InMemorySessionService()
    runner = Runner(app_name="verify_m4", agent=critic_agent, session_service=sessions)
    session = await sessions.create_session(app_name="verify_m4", user_id="verifier")
    texts, calls, responses = [], [], []
    async for event in runner.run_async(user_id="verifier", session_id=session.id,
                                        new_message=types.Content(role="user", parts=[types.Part(text=message)])):
        for call in event.get_function_calls():
            calls.append({"name": call.name, "args": dict(call.args or {})})
        for resp in event.get_function_responses():
            responses.append({"name": resp.name, "response": dict(resp.response or {})})
        if event.is_final_response() and event.content and event.content.parts:
            texts.extend(p.text for p in event.content.parts if p.text and not p.thought)
    final = await sessions.get_session(app_name="verify_m4", user_id="verifier", session_id=session.id)
    return {"final_text": "\n".join(texts).strip(), "tool_calls": calls, "tool_responses": responses,
            "guard": final.state.get(GUARD_KEY)}


def case_checks(case: str, kind: str, uris: list[str], r: dict) -> list[dict]:
    text = r["final_text"]
    reviews = [x["response"] for x in r["tool_responses"] if x["name"] == "review_image"]
    called = [c["args"].get("gcs_uri") for c in r["tool_calls"] if c["name"] == "review_image"]
    guard = r["guard"] or {}
    out = [check("final response is not empty", bool(text), f"{len(text)} chars")]
    try:
        v = parse_verdict(text)
    except VerdictParseError as e:
        return out + [check("verdict parses in the exact POSTS / VISUALS / OVERALL format", False, str(e))]
    out.append(check("verdict parses in the exact POSTS / VISUALS / OVERALL format", True))
    out.append(check("guard parsed the model's verdict", guard.get("parsed") is True, json.dumps(guard)))
    corrections = guard.get("corrections") or []
    out.append(check("model's derived fields needed no correction (informational)", True,
                     f"{len(corrections)} corrections: {corrections}" if corrections else "0 corrections"))

    out.append(check("review_image called exactly once per image",
                     sorted(called) == sorted(uris) and len(called) == len(set(called)),
                     f"{len(called)} calls for {len(uris)} images"))
    ok_reviews = [x for x in reviews if x.get("status") == "success"]
    out.append(check("every review succeeded", len(ok_reviews) == len(uris),
                     f"errors: {[x.get('error') for x in reviews if x.get('status') != 'success']}"))
    try:
        _, residual = enforce(text, reviews, uris)
    except VerdictParseError as e:
        residual = [str(e)]
    out.append(check("final verdict is consistent with the review results and the rules", residual == [],
                     f"{residual}" if residual else ""))
    any_failed = len(ok_reviews) < len(reviews) or len(reviews) < len(uris)
    out.append(check("no [REVIEW_ERROR] unless a review actually failed",
                     any_failed or "[REVIEW_ERROR]" not in text))
    tool_scores = [x["score"] for x in ok_reviews]
    if uris:
        out.append(check("VISUALS score is the lowest review score",
                         v.visuals["Score"] == f"{min(tool_scores)}/10" if tool_scores else False,
                         f"VISUALS {v.visuals['Score']}, review scores {tool_scores}"))
    if v.overall["All Approved"] == "NO":
        out.append(check("NO verdict has a non-empty Priority Revisions",
                         v.overall["Priority Revisions"].strip().lower() not in ("", "none", "n/a"),
                         v.overall["Priority Revisions"][:200]))

    base = case.removesuffix("_2")
    if base == "critic_strong":
        out.append(check("strong campaign is APPROVED", v.overall["All Approved"] == "YES",
                         f"POSTS {v.posts['Score']} {v.posts['Status']}, VISUALS {v.visuals['Score']} {v.visuals['Status']}"))
    elif base == "critic_weak":
        out.append(check("weak campaign is NEEDS_REVISION", v.overall["All Approved"] == "NO"
                         and v.posts["Status"] == "NEEDS_REVISION", f"POSTS {v.posts['Score']}"))
        logo_text = " ".join(f"{x.get('issues', '')} {x.get('suggestions', '')}" for x in ok_reviews)
        out.append(check("image review flags the real logo / trademark", bool(LOGO_RE.search(logo_text)),
                         logo_text[:200]))
    elif base == "critic_mixed":
        out.append(check("mixed visuals -> NEEDS_REVISION", v.visuals["Status"] == "NEEDS_REVISION",
                         f"review statuses {[x.get('approval_status') for x in ok_reviews]}"))
    elif base == "critic_no_images":
        out.append(check("no images -> VISUALS N/A + NOT_REVIEWED, no review calls",
                         v.visuals["Score"] == "N/A" and v.visuals["Status"] == "NOT_REVIEWED" and not called))
        out.append(check("NOT_REVIEWED treated as approved",
                         v.overall["All Approved"] == ("YES" if v.posts["Status"] == "APPROVED" else "NO")))
    elif base == "critic_weak_posts_good_visuals":
        out.append(check("weak posts -> POSTS NEEDS_REVISION and overall NO",
                         v.posts["Status"] == "NEEDS_REVISION" and v.overall["All Approved"] == "NO",
                         f"POSTS {v.posts['Score']}"))
        out.append(check("good visual still judged on its own (VISUALS APPROVED)", v.visuals["Status"] == "APPROVED",
                         f"VISUALS {v.visuals['Score']} {v.visuals['Status']}"))
    return out


# ---------------------------------------------------------------- evidence


def write_case(out_dir: pathlib.Path, case: str, data: dict) -> None:
    (out_dir / f"{case}.json").write_text(redact(json.dumps(data, indent=2, default=str)))
    lines = [f"# {data['title']}", "", f"Case `{case}`, run {data['ran_at']}, model `{data['model']}`, "
             f"commit `{data['commit']}`.", "", "## Checks", ""]
    lines += [f"- {'PASS' if c['ok'] else 'FAIL'}: {c['check']}" + (f" ({c['detail']})" if c["detail"] else "")
              for c in data["checks"]]
    reviews = [x["response"] for x in data["tool_responses"] if x["name"] == "review_image"]
    if reviews:
        lines += ["", "## review_image results", "", "| concept | score | status | override | issues |", "|---|---|---|---|---|"]
        for x in reviews:
            override = f"model said {x['model_approval_status']}" if x.get("status_overridden") else "-"
            issues = (x.get("issues") or x.get("error") or "").replace("|", "/").replace("\n", " ")
            lines.append(f"| {x.get('concept_name')} | {x.get('score', '-')} | {x.get('approval_status', x.get('status'))} | "
                         f"{override} | {issues[:300]} |")
    guard = data.get("guard") or {}
    lines += ["", "## Verdict guard", "", f"- parsed: {guard.get('parsed')}",
              f"- corrections: {guard.get('corrections') or 'none'}"]
    lines += ["", "## Input", "", "```text", data["message"], "```", "", "## Final verdict", "", data["final_text"], ""]
    (out_dir / f"{case}.md").write_text(redact("\n".join(lines)))


def write_summary(out_dir: pathlib.Path, log: list) -> None:
    lines = ["# Milestone 4 verification summary", "",
             "Generated by `workshop/starter/scripts/verify_m4.py`. Bucket, project ID and number redacted.", "",
             "| Case | Checks passed | POSTS | VISUALS | All Approved | Overall | Guard corrections |",
             "|---|---|---|---|---|---|---|"]
    commits = set()
    for case, (title, _, _) in CASES.items():
        path = out_dir / f"{case}.json"
        if not path.exists():
            lines.append(f"| [{case}]({case}.md) | not run | | | | | |")
            continue
        d = json.loads(path.read_text())
        commits.add(d["commit"])
        ok = sum(c["ok"] for c in d["checks"])
        try:
            v = parse_verdict(d["final_text"])
            cols = (f"{v.posts['Score']} {v.posts['Status']}", f"{v.visuals['Score']} {v.visuals['Status']}",
                    v.overall["All Approved"], v.overall["Overall Score"])
        except VerdictParseError:
            cols = ("unparseable",) * 4
        n_corr = len((d.get("guard") or {}).get("corrections") or [])
        lines.append(f"| [{case}]({case}.md): {title} | {ok}/{len(d['checks'])} | " + " | ".join(cols) + f" | {n_corr} |")
    used = sum(1 for e in log if e.get("counted", True))
    lines += ["", f"Code commit for these runs: {', '.join(f'`{c}`' for c in sorted(commits)) or 'unknown'}", "",
              f"Critic runs used: {used} (plus {len(log) - used} rejected with 403, not counted). "
              f"Full history: [run_log.json](run_log.json).", ""]
    notes = out_dir / "NOTES.md"
    if notes.exists():
        lines += [notes.read_text().strip(), ""]
    (out_dir / "README.md").write_text("\n".join(lines))


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", nargs="+", choices=list(CASES))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--budget", type=int, default=10, help="max Critic runs in total (403s not counted)")
    parser.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "run_log.json"
    log = json.loads(log_path.read_text()) if log_path.exists() else []

    for case, (title, kind, images) in CASES.items():
        if args.only and case not in args.only:
            continue
        if (out_dir / f"{case}.json").exists() and not args.force:
            print(f"[skip] {case}: cached")
            continue
        if sum(1 for e in log if e.get("counted", True)) >= args.budget:
            print(f"[stop] budget of {args.budget} Critic runs reached; not running {case}")
            break
        message, uris = build_message(strong_captions() if kind == "strong" else WEAK_CAPTIONS, images)
        log.append({"case": case, "at": datetime.datetime.now().isoformat(timespec="seconds")})
        log_path.write_text(json.dumps(log, indent=2))
        print(f"[run ] {case} ...", flush=True)
        try:
            result = await run_critic(message)
        except Exception as exc:
            log[-1]["error"] = redact(f"{type(exc).__name__}: {exc}")[:300]
            if getattr(exc, "code", None) == 403:
                log[-1]["counted"] = False
            log_path.write_text(json.dumps(log, indent=2))
            print(f"[fail] {case}: {log[-1]['error']}")
            break
        checks = case_checks(case, kind, uris, result)
        data = {"title": title, "message": message, **result, "checks": checks, "ran_at": log[-1]["at"],
                "model": critic_agent.model, "commit": git_commit()}
        write_case(out_dir, case, data)
        failed = [c["check"] for c in checks if not c["ok"]]
        print(f"[done] {case}: {len(checks) - len(failed)}/{len(checks)} checks passed"
              + (f"; FAILED: {failed}" if failed else ""))
        errors = [x for x in result["tool_responses"] if x["name"] == "review_image"
                  and x["response"].get("status") != "success"]
        if errors:
            print(f"[stop] review_image failed: {redact(str(errors[0]['response'].get('error')))[:300]}")
            break

    write_summary(out_dir, log)
    used = sum(1 for e in log if e.get("counted", True))
    print(f"Critic runs used: {used}/{args.budget}. Evidence: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
