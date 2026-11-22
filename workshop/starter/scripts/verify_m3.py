"""Milestone 3 verification: the Designer generates real images into GCS.

Cases:
  tool_square        generate_image called directly with "1:1"  (1 image)
  tool_portrait      generate_image called directly with "4:5"  (1 image)
  designer_sequence  Designer agent with the committed M2 research + 3 captions
                     in the conversation history (expected 3 images)

For every image the script checks the tool result (gcs_uri, no bytes), that the
object really exists in the bucket, and its pixel ratio read from the file header.
Evidence goes to project-docs/evidence/m3/, with the bucket, project ID, project
number and emails redacted. Compressed JPEG copies go to images/.

Budget is counted in generated images (--budget, default 10). A guard blocks any
generate_image call past the budget; calls rejected with HTTP 403 are not counted.

Usage (from workshop/starter/):
  uv run python scripts/verify_m3.py
  uv run python scripts/verify_m3.py --only tool_square
"""

import argparse
import asyncio
import datetime
import json
import os
import pathlib
import re
import struct
import subprocess
import sys
import time
import uuid

STARTER_DIR = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = STARTER_DIR.parents[1]
DEFAULT_OUT = REPO_ROOT / "project-docs" / "evidence" / "m3"
M2_EVIDENCE = REPO_ROOT / "project-docs" / "evidence" / "m2"

sys.path.insert(0, str(STARTER_DIR / "agents"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(STARTER_DIR / ".env")

from google.adk.artifacts import InMemoryArtifactService  # noqa: E402
from google.adk.events import Event  # noqa: E402
from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions import InMemorySessionService  # noqa: E402
from google.cloud import storage  # noqa: E402
from google.genai import types  # noqa: E402

from designer import image_gen_tool  # noqa: E402
from designer.agent import root_agent as designer_agent  # noqa: E402

BUCKET = os.environ["GCS_IMAGES_BUCKET"]
TOLERANCE = 0.03
EXPECTED_RATIO = {"1:1": 1.0, "4:5": 0.8}
DESIGN_REQUEST = "Create the visual concepts and generate one image for each of the three captions above."
SQUARE_PROMPT = (
    "Photorealistic flat lay of a matte sage-green reusable water bottle on a light oak desk next to "
    "an open notebook and a small succulent, soft morning window light, minimalist, airy."
)
PORTRAIT_PROMPT = (
    "Photorealistic portrait shot of a college student walking across a sunny campus quad holding a "
    "matte sage-green reusable water bottle, shallow depth of field, warm golden-hour light."
)


# ---------------------------------------------------------------- helpers


def redact(text: str) -> str:
    # Bucket first: its name contains the project ID.
    for key, tag in (("GCS_IMAGES_BUCKET", "<BUCKET>"), ("GOOGLE_CLOUD_PROJECT_NUMBER", "<PROJECT_NUMBER>"),
                     ("GOOGLE_CLOUD_PROJECT", "<PROJECT_ID>")):
        value = os.getenv(key)
        if value:
            text = text.replace(value, tag)
    return re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "<EMAIL>", text)


def check(name: str, ok: bool, detail: str = "") -> dict:
    return {"check": name, "ok": bool(ok), "detail": detail}


def image_size(data: bytes) -> tuple[int, int] | None:
    """Width and height from a PNG or JPEG header (no image library needed)."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            length = struct.unpack(">H", data[i + 2:i + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack(">HH", data[i + 5:i + 9])
                return width, height
            i += 2 + length
    return None


def git_commit() -> str:
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True).stdout.strip()
    dirty = git("status", "--porcelain", "--", "workshop/starter/agents", "workshop/starter/scripts")
    return git("rev-parse", "--short", "HEAD") + (" (uncommitted changes in agents/ or scripts/)" if dirty else "")


def compress_copy(data: bytes, dest: pathlib.Path) -> int:
    """Writes a JPEG copy under 300 KB using macOS sips; returns its size."""
    tmp = dest.with_suffix(".src")
    tmp.write_bytes(data)
    try:
        for max_side, quality in ((1080, 70), (1080, 55), (800, 55), (640, 50)):
            subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", str(quality),
                            "-Z", str(max_side), str(tmp), "--out", str(dest)],
                           check=True, capture_output=True)
            if dest.stat().st_size < 300_000:
                break
    finally:
        tmp.unlink(missing_ok=True)
    return dest.stat().st_size


class Budget:
    def __init__(self, log_path: pathlib.Path, limit: int):
        self.path, self.limit = log_path, limit
        self.log = json.loads(log_path.read_text()) if log_path.exists() else []

    def used(self) -> int:
        return sum(1 for e in self.log if e.get("counted", True))

    def record(self, case: str, result: dict, seconds: float) -> None:
        entry = {"case": case, "at": datetime.datetime.now().isoformat(timespec="seconds"),
                 "seconds": round(seconds, 1), "status": result.get("status")}
        if result.get("status") != "success":
            entry["error"] = redact(str(result.get("error")))[:300]
            # A 403 never reached the model; it is logged but not counted.
            if "403" in entry["error"] and "PERMISSION_DENIED" in entry["error"]:
                entry["counted"] = False
        self.log.append(entry)
        self.path.write_text(json.dumps(self.log, indent=2))


class NullToolContext:
    """Direct tool calls have no artifact service; the tool skips the artifact."""

    async def save_artifact(self, filename, artifact):
        raise ValueError("no artifact service")


# ---------------------------------------------------------------- image checks


def verify_image(result: dict, expected_ratio: str, out_dir: pathlib.Path, copy_name: str) -> tuple[list, dict]:
    info = {"gcs_uri": result.get("gcs_uri"), "copy": None, "size": None, "bytes": None, "content_type": None}
    checks = [
        check("tool returned status success", result.get("status") == "success", result.get("error", "")),
        check("tool result contains no bytes",
              all(not isinstance(v, (bytes, bytearray)) for v in result.values())
              and len(json.dumps(result, default=str)) < 1000),
    ]
    uri = result.get("gcs_uri") or ""
    prefix = f"gs://{BUCKET}/campaign-images/"
    checks.append(check("gcs_uri points into the images bucket", uri.startswith(prefix), uri))
    if not uri.startswith(prefix):
        return checks, info

    blob = storage.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT")).bucket(BUCKET).get_blob(uri[len(f"gs://{BUCKET}/"):])
    checks.append(check("object exists in the bucket", blob is not None))
    if blob is None:
        return checks, info
    data = blob.download_as_bytes()
    size = image_size(data)
    info |= {"bytes": blob.size, "content_type": blob.content_type, "size": size}
    checks.append(check("object is a real image (PNG/JPEG header parsed)", size is not None,
                        f"{blob.content_type}, {blob.size} bytes"))
    if size:
        ratio, want = size[0] / size[1], EXPECTED_RATIO[expected_ratio]
        checks.append(check(f"pixel ratio matches {expected_ratio} (±{int(TOLERANCE * 100)}%)",
                            abs(ratio - want) / want <= TOLERANCE, f"{size[0]}x{size[1]} = {ratio:.3f}"))
        images_dir = out_dir / "images"
        images_dir.mkdir(exist_ok=True)
        copy = images_dir / f"{copy_name}.jpg"
        kb = compress_copy(data, copy) / 1000
        info["copy"] = f"images/{copy.name}"
        checks.append(check("compressed copy under 300 KB", kb < 300, f"{kb:.0f} KB"))
    return checks, info


# ---------------------------------------------------------------- cases


async def run_tool_case(case: str, ratio: str, prompt: str, out_dir: pathlib.Path, budget: Budget) -> dict:
    t0 = time.monotonic()
    result = await image_gen_tool.generate_image(case, prompt, ratio, NullToolContext())
    seconds = time.monotonic() - t0
    budget.record(case, result, seconds)
    checks, info = verify_image(result, ratio, out_dir, case)
    return {"prompt": prompt, "aspect_ratio": ratio, "tool_result": result, "images": [info | {"seconds": round(seconds, 1)}],
            "final_text": "", "tool_calls": [], "checks": checks}


async def run_designer_case(out_dir: pathlib.Path, budget: Budget) -> dict:
    research = json.loads((M2_EVIDENCE / "strategist_b.json").read_text())
    captions = json.loads((M2_EVIDENCE / "sequence_b.json").read_text())

    timings, results, blocked = {}, [], []

    def before_tool(tool, args, tool_context):
        if tool.name != "generate_image":
            return None
        if budget.used() >= budget.limit:
            blocked.append(args.get("concept_name"))
            return {"status": "error", "error": "verify_m3 image budget reached; not generated"}
        timings[tool_context.function_call_id] = time.monotonic()
        return None

    def after_tool(tool, args, tool_context, tool_response):
        if tool.name == "generate_image":
            seconds = time.monotonic() - timings.pop(tool_context.function_call_id, time.monotonic())
            budget.record("designer_sequence", tool_response, seconds)
            results.append({"args": dict(args), "result": dict(tool_response), "seconds": round(seconds, 1)})
        return None

    agent = designer_agent.model_copy(update={"before_tool_callback": before_tool, "after_tool_callback": after_tool})
    sessions = InMemorySessionService()
    runner = Runner(app_name="verify_m3", agent=agent, session_service=sessions,
                    artifact_service=InMemoryArtifactService())
    session = await sessions.create_session(app_name="verify_m3", user_id="verifier")
    for author, text in (("brand_strategist", research["final_text"]), ("copywriter", captions["final_text"])):
        await sessions.append_event(session, Event(
            invocation_id=f"e-{uuid.uuid4()}", author=author,
            content=types.Content(role="model", parts=[types.Part(text=text)])))

    texts, tool_calls = [], []
    async for event in runner.run_async(user_id="verifier", session_id=session.id,
                                        new_message=types.Content(role="user", parts=[types.Part(text=DESIGN_REQUEST)])):
        for call in event.get_function_calls():
            tool_calls.append({"name": call.name, "args": dict(call.args or {})})
        if event.is_final_response() and event.content and event.content.parts:
            texts.extend(p.text for p in event.content.parts if p.text and not p.thought)
    final_text = "\n".join(texts).strip()

    captions_in = len(re.findall(r"^\*\*Caption\s+\d+:", captions["final_text"], re.MULTILINE))
    image_calls = [c for c in tool_calls if c["name"] == "generate_image"]
    checks = [
        check("final response is not empty", bool(final_text), f"{len(final_text)} chars"),
        check(f"one generate_image call per caption ({captions_in} captions)", len(image_calls) == captions_in,
              f"{len(image_calls)} calls" + (f"; blocked by budget: {blocked}" if blocked else "")),
        check("every call passed a supported aspect_ratio",
              all(image_gen_tool.normalize_aspect_ratio(c["args"].get("aspect_ratio", "")) for c in image_calls),
              f"{[c['args'].get('aspect_ratio') for c in image_calls]}"),
        check("no raw image bytes in the response (no long base64 run, under 20k chars)",
              len(final_text) < 20_000 and not re.search(r"[A-Za-z0-9+/=]{400,}", final_text)),
    ]
    images = []
    for n, r in enumerate(results, 1):
        ratio = image_gen_tool.normalize_aspect_ratio(r["args"].get("aspect_ratio", "")) or "1:1"
        img_checks, info = verify_image(r["result"], ratio, out_dir, f"designer_sequence-{n}")
        checks += [check(f"image {n}: {c['check']}", c["ok"], c["detail"]) for c in img_checks]
        uri = r["result"].get("gcs_uri")
        checks.append(check(f"image {n}: gcs_uri reported in the response", bool(uri) and uri in final_text))
        images.append(info | {"seconds": r["seconds"], "aspect_ratio": ratio, "concept_name": r["args"].get("concept_name")})
    return {"prompt": DESIGN_REQUEST, "history": ["strategist_b (M2 evidence)", "sequence_b captions (M2 evidence)"],
            "final_text": final_text, "tool_calls": tool_calls, "images": images, "checks": checks}


# ---------------------------------------------------------------- evidence


def write_case(out_dir: pathlib.Path, case: str, title: str, data: dict) -> None:
    (out_dir / f"{case}.json").write_text(redact(json.dumps(data, indent=2, default=str)))
    lines = [f"# {title}", "", f"Case `{case}`, run {data['ran_at']}, text model `{data['text_model']}`, "
             f"image model `{data['image_model']}`, commit `{data['commit']}`.", "", "## Checks", ""]
    lines += [f"- {'PASS' if c['ok'] else 'FAIL'}: {c['check']}" + (f" ({c['detail']})" if c["detail"] else "")
              for c in data["checks"]]
    lines += ["", "## Images", "", "| # | gcs_uri | ratio asked | pixels | type | seconds | copy |", "|---|---|---|---|---|---|---|"]
    for n, img in enumerate(data["images"], 1):
        px = f"{img['size'][0]}x{img['size'][1]}" if img.get("size") else "-"
        copy = f"![{case} {n}]({img['copy']})" if img.get("copy") else "-"
        lines.append(f"| {n} | `{img.get('gcs_uri')}` | {img.get('aspect_ratio', data.get('aspect_ratio'))} | {px} | "
                     f"{img.get('content_type') or '-'} | {img.get('seconds')} | {copy} |")
    lines += ["", "## Prompt", "", "```text", data["prompt"], "```", ""]
    if data.get("tool_calls"):
        lines += ["## Tool calls", ""] + [f"- `{c['name']}` {json.dumps(c['args'])}" for c in data["tool_calls"]] + [""]
    if data.get("final_text"):
        lines += ["## Final response", "", data["final_text"], ""]
    (out_dir / f"{case}.md").write_text(redact("\n".join(lines)))


def write_summary(out_dir: pathlib.Path, budget: Budget) -> None:
    lines = ["# Milestone 3 verification summary", "",
             "Generated by `workshop/starter/scripts/verify_m3.py`. Bucket, project ID and number redacted.", "",
             "| Case | Checks passed | Images | Seconds per image |", "|---|---|---|---|"]
    commits = set()
    for case, title in CASES.items():
        path = out_dir / f"{case}.json"
        if not path.exists():
            lines.append(f"| [{case}]({case}.md) | not run | | |")
            continue
        data = json.loads(path.read_text())
        commits.add(data["commit"])
        ok = sum(c["ok"] for c in data["checks"])
        secs = ", ".join(str(i.get("seconds")) for i in data["images"]) or "-"
        lines.append(f"| [{case}]({case}.md): {title} | {ok}/{len(data['checks'])} | {len(data['images'])} | {secs} |")
    lines += ["", f"Code commit for these runs: {', '.join(f'`{c}`' for c in sorted(commits)) or 'unknown'}", "",
              f"Images generated (counted): {budget.used()} of {budget.limit}. "
              f"Full history: [run_log.json](run_log.json).", ""]
    notes = out_dir / "NOTES.md"
    if notes.exists():
        lines += [notes.read_text().strip(), ""]
    (out_dir / "README.md").write_text("\n".join(lines))


CASES = {
    "tool_square": "generate_image called directly, 1:1",
    "tool_portrait": "generate_image called directly, 4:5",
    "designer_sequence": "Designer agent after M2 research and captions",
}
EXPECTED_IMAGES = {"tool_square": 1, "tool_portrait": 1, "designer_sequence": 3}


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", nargs="+", choices=list(CASES))
    parser.add_argument("--force", action="store_true", help="rerun cases already cached")
    parser.add_argument("--budget", type=int, default=10, help="max generated images in total (403s not counted)")
    parser.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    budget = Budget(out_dir / "run_log.json", args.budget)

    for case in CASES:
        if args.only and case not in args.only:
            continue
        if (out_dir / f"{case}.json").exists() and not args.force:
            print(f"[skip] {case}: cached (use --force to rerun)")
            continue
        if budget.used() + EXPECTED_IMAGES[case] > budget.limit:
            print(f"[stop] {case} needs {EXPECTED_IMAGES[case]} images; {budget.used()}/{budget.limit} used")
            break
        print(f"[run ] {case} ...", flush=True)
        if case == "tool_square":
            data = await run_tool_case(case, "1:1", SQUARE_PROMPT, out_dir, budget)
        elif case == "tool_portrait":
            data = await run_tool_case(case, "4:5", PORTRAIT_PROMPT, out_dir, budget)
        else:
            data = await run_designer_case(out_dir, budget)
        data |= {"ran_at": datetime.datetime.now().isoformat(timespec="seconds"), "commit": git_commit(),
                 "text_model": designer_agent.model, "image_model": os.environ.get("GEMINI_IMAGE_MODEL")}
        write_case(out_dir, case, CASES[case], data)
        failed = [c["check"] for c in data["checks"] if not c["ok"]]
        print(f"[done] {case}: {len(data['checks']) - len(failed)}/{len(data['checks'])} checks passed"
              + (f"; FAILED: {failed}" if failed else ""))
        errors = [i for i in data.get("images", []) if not i.get("gcs_uri")] or (
            [data["tool_result"]] if data.get("tool_result", {}).get("status") == "error" else [])
        if errors:
            print(f"[stop] {case} had a failed image; stopping. Last error: "
                  f"{redact(str(data.get('tool_result', {}).get('error', '')))[:200]}")
            break

    write_summary(out_dir, budget)
    print(f"Images used: {budget.used()}/{budget.limit}. Evidence: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
