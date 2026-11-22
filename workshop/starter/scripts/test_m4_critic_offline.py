"""Offline tests for the Critic: review tool, verdict parser/guard, callbacks.

No paid calls, no cloud access.

Usage (from workshop/starter/):
  uv run python scripts/test_m4_critic_offline.py
"""

import asyncio
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "agents"))
os.environ.setdefault("GEMINI_MODEL", "test-text-model")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test-project")

from google.adk.models import LlmResponse  # noqa: E402
from google.genai import errors, types  # noqa: E402

from critic import agent as critic_agent  # noqa: E402
from critic import image_review_tool as tool  # noqa: E402
from critic.retry import REVIEW_HTTP_OPTIONS  # noqa: E402
from critic.verdict import VerdictParseError, enforce, parse_verdict  # noqa: E402

FAILURES = []
A = "gs://b/campaign-images/a.png"
B = "gs://b/campaign-images/b.png"


def expect(name: str, ok: bool, detail: object = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" ({detail})" if detail != "" else ""))
    if not ok:
        FAILURES.append(name)


def verdict_text(posts="8/10", posts_status="APPROVED", vis="8/10", vis_status="APPROVED",
                 vis_issues="None", all_ok="YES", priority="None", overall="8/10") -> str:
    return f"""Here is my review.

**POSTS REVIEW:**
- Score: {posts}
- Status: {posts_status}
- What Works: Strong hooks.
- Issues: None
- Suggestions: None

**VISUALS REVIEW:**
- Score: {vis}
- Status: {vis_status}
- What Works: Warm light.
- Issues: {vis_issues}
- Suggestions: Remove the logo.

**OVERALL ASSESSMENT:**
- All Approved: {all_ok}
- Priority Revisions: {priority}
- Overall Score: {overall}
"""


def ok_review(uri, score, status=None, name="c"):
    return {"status": "success", "gcs_uri": uri, "concept_name": name, "score": score,
            "approval_status": status or ("APPROVED" if score >= 7 else "NEEDS_REVISION")}


# ------------------------------------------------------------ verdict guard


def test_verdict() -> None:
    text, fixes = enforce(verdict_text(), [ok_review(A, 8), ok_review(B, 9)], [A, B])
    expect("consistent verdict is left unchanged", fixes == [] and text == verdict_text(), fixes)

    text, fixes = enforce(verdict_text(vis="8/10", overall="8/10"),
                          [ok_review(A, 8), ok_review(B, 5)], [A, B])
    v = parse_verdict(text)
    expect("lowest image score wins and drives NEEDS_REVISION",
           v.visuals["Score"] == "5/10" and v.visuals["Status"] == "NEEDS_REVISION"
           and v.overall["All Approved"] == "NO" and v.overall["Overall Score"] == "5/10", fixes)
    expect("empty Priority Revisions filled from VISUALS suggestions when verdict flips to NO",
           v.overall["Priority Revisions"] == "Visuals: Remove the logo.", v.overall["Priority Revisions"])

    text, fixes = enforce(verdict_text(vis="7/10"), [ok_review(A, 7), {"status": "error", "gcs_uri": B,
                                                     "concept_name": "cB", "error": "403"}], [A, B])
    v = parse_verdict(text)
    expect("review error -> NEEDS_REVISION with [REVIEW_ERROR] in Issues and Priority",
           v.visuals["Status"] == "NEEDS_REVISION" and v.visuals["Issues"].startswith("[REVIEW_ERROR]")
           and v.overall["Priority Revisions"].startswith("[REVIEW_ERROR]") and v.overall["All Approved"] == "NO",
           fixes)

    text, fixes = enforce(verdict_text(), [ok_review(A, 9)], [A, B])
    v = parse_verdict(text)
    expect("image in the input but never reviewed counts as a review error",
           v.visuals["Status"] == "NEEDS_REVISION" and B in v.visuals["Issues"], v.visuals["Issues"])

    text, fixes = enforce(verdict_text(vis="7/10", vis_status="APPROVED"), [], [])
    v = parse_verdict(text)
    expect("no images -> N/A + NOT_REVIEWED, treated as approved",
           v.visuals["Score"] == "N/A" and v.visuals["Status"] == "NOT_REVIEWED"
           and v.overall["All Approved"] == "YES" and v.overall["Overall Score"] == "8/10", fixes)

    text, fixes = enforce(verdict_text(posts="6/10", posts_status="APPROVED", overall="6/10"),
                          [ok_review(A, 9)], [A])
    v = parse_verdict(text)
    expect("POSTS status follows POSTS score (6 -> NEEDS_REVISION)",
           v.posts["Status"] == "NEEDS_REVISION" and v.overall["All Approved"] == "NO"
           and v.overall["Priority Revisions"].startswith("Posts:"), fixes)

    bolded = verdict_text().replace("- Score:", "- **Score:**")
    expect("bold field names still parse", parse_verdict(bolded).posts["Score"] == "8/10")

    for name, broken in [
        ("missing section", verdict_text().replace("**VISUALS REVIEW:**", "VISUALS")),
        ("sections out of order", verdict_text().replace("**POSTS REVIEW:**", "**TMP:**")
            .replace("**OVERALL ASSESSMENT:**", "**POSTS REVIEW:**").replace("**TMP:**", "**OVERALL ASSESSMENT:**")),
        ("score not X/10", verdict_text(posts="eight")),
        ("score out of range", verdict_text(posts="11/10")),
        ("bad status", verdict_text(posts_status="OK")),
        ("missing field", verdict_text().replace("- Suggestions: None\n", "", 1)),
    ]:
        try:
            parse_verdict(broken)
            expect(f"unparseable: {name} raises", False)
        except VerdictParseError as e:
            expect(f"unparseable: {name} raises", True, e)


# ------------------------------------------------------------ review tool


class FakeModels:
    def __init__(self, behaviour):
        self.behaviour, self.calls = behaviour, []

    async def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        return type("R", (), {"text": self.behaviour})()


class FakeClient:
    last = None

    def __init__(self, behaviour):
        self.aio = type("Aio", (), {})()
        self.aio.models = FakeModels(behaviour)
        FakeClient.last = self


async def run_review(behaviour, uri=A):
    tool.genai.Client = lambda **kw: FakeClient(behaviour)
    return await tool.review_image(uri, "caption1", "Coffee roastery for remote workers")


async def test_tool() -> None:
    real = tool.genai.Client
    try:
        good = '{"score": 8, "approval_status": "APPROVED", "what_works": "w", "issues": "None", "suggestions": "None"}'
        r = await run_review(good)
        call = FakeClient.last.aio.models.calls[0]
        expect("success returns a plain dict with score and status",
               isinstance(r, dict) and r["status"] == "success" and r["score"] == 8
               and r["approval_status"] == "APPROVED" and r["gcs_uri"] == A, r)
        expect("image passed by URI (Part.from_uri), prompt second",
               call["contents"][0].file_data.file_uri == A and isinstance(call["contents"][1], str))
        expect("prompt asks about logos/trademarks and garbled text",
               "logos or trademarks" in call["contents"][1] and "Garbled or misspelled text" in call["contents"][1])
        expect("forced JSON schema + retry options attached",
               call["config"].response_mime_type == "application/json"
               and call["config"].response_schema is tool._GeminiReview
               and call["config"].http_options is REVIEW_HTTP_OPTIONS
               and REVIEW_HTTP_OPTIONS.retry_options.attempts == 3)
        expect("model read from GEMINI_MODEL", call["model"] == "test-text-model")

        r = await run_review('{"score": 6, "approval_status": "APPROVED", "what_works": "w", "issues": "i", "suggestions": "s"}')
        expect("score 6 + APPROVED -> NEEDS_REVISION, override recorded",
               r["approval_status"] == "NEEDS_REVISION" and r["status_overridden"] is True
               and r["model_approval_status"] == "APPROVED", r)
        r = await run_review('{"score": 7, "approval_status": "NEEDS_REVISION", "what_works": "w", "issues": "i", "suggestions": "s"}')
        expect("score 7 + NEEDS_REVISION -> APPROVED, override recorded",
               r["approval_status"] == "APPROVED" and r["status_overridden"] is True, r)

        r = await run_review("not json")
        expect("invalid JSON returns an error dict", r["status"] == "error" and "ValidationError" in r["error"], r)
        r = await run_review(errors.ClientError(403, {"error": {"code": 403, "message": "denied", "status": "PERMISSION_DENIED"}}))
        expect("API error returns an error dict (no crash)", r["status"] == "error" and "403" in r["error"], r)
        r = await run_review(good, uri="https://example.com/a.png")
        expect("non-GCS URI rejected before any call", r["status"] == "error" and "Not a GCS URI" in r["error"], r)
    finally:
        tool.genai.Client = real


# ------------------------------------------------------------ callbacks


class FakeState(dict):
    def to_dict(self):
        return dict(self)


class FakeContext:
    def __init__(self, user_text="", invocation_id="inv-1", function_call_id=None, state=None):
        self.state = FakeState(state or {})
        self.invocation_id, self.function_call_id = invocation_id, function_call_id
        self.user_content = types.Content(role="user", parts=[types.Part(text=user_text)])


def text_response(text, partial=False):
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]), partial=partial)


def test_callbacks() -> None:
    ctx = FakeContext(f"Review these. Images: **{A}**, `{B}`. Also {A}.", invocation_id="inv-2")
    ctx.state.update({"critic_review:inv-1:c1": ok_review(A, 2), "critic_guard": {"old": True}})
    critic_agent.reset_review_state(ctx)
    expect("reset clears the previous run's reviews and finds each distinct gs:// URI once",
           ctx.state["critic_review:inv-1:c1"] is None and critic_agent.current_reviews(ctx) == []
           and ctx.state["critic_expected_uris"] == [A, B] and ctx.state["critic_guard"] is None,
           ctx.state["critic_expected_uris"])

    # ADK runs parallel tool calls concurrently; each call gets its own copy of the
    # state and the deltas are merged afterwards. Simulate exactly that.
    review_tool = type("T", (), {"name": "review_image"})()
    t1 = FakeContext(invocation_id="inv-2", function_call_id="call-a", state=ctx.state)
    t2 = FakeContext(invocation_id="inv-2", function_call_id="call-b", state=ctx.state)
    critic_agent.record_review(review_tool, {"gcs_uri": A}, t1, ok_review(A, 9))
    critic_agent.record_review(review_tool, {"gcs_uri": B}, t2, ok_review(B, 4))
    critic_agent.record_review(type("T", (), {"name": "other"})(), {}, t2, {"x": 1})
    ctx.state.update(t1.state)
    ctx.state.update(t2.state)
    reviews = critic_agent.current_reviews(ctx)
    expect("parallel review_image calls are both kept (one key per call)",
           sorted(r["gcs_uri"] for r in reviews) == [A, B], [r["gcs_uri"] for r in reviews])
    other = FakeContext(invocation_id="inv-3", state=ctx.state)
    expect("reviews from another run are ignored", critic_agent.current_reviews(other) == [])

    fc = LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name="review_image", args={}))]))
    expect("guard ignores function-call responses", critic_agent.enforce_verdict(ctx, fc) is None)
    expect("guard ignores partial responses", critic_agent.enforce_verdict(ctx, text_response(verdict_text(), partial=True)) is None)

    out = critic_agent.enforce_verdict(ctx, text_response(verdict_text()))
    v = parse_verdict(out.content.parts[-1].text)
    expect("guard rewrites the final verdict from recorded reviews (no false [REVIEW_ERROR])",
           v.visuals["Score"] == "4/10" and v.overall["All Approved"] == "NO"
           and "[REVIEW_ERROR]" not in out.content.parts[-1].text
           and ctx.state["critic_guard"]["parsed"] and ctx.state["critic_guard"]["corrections"], v.visuals)

    junk = "I think it's great!"
    expect("unparseable final text left unchanged",
           critic_agent.enforce_verdict(ctx, text_response(junk)) is None
           and ctx.state["critic_guard"]["parsed"] is False, ctx.state["critic_guard"])


async def main() -> int:
    print("== verdict guard")
    test_verdict()
    print("== review tool (fake client)")
    await test_tool()
    print("== callbacks")
    test_callbacks()
    print(f"\n{'ALL PASSED' if not FAILURES else f'{len(FAILURES)} FAILED: {FAILURES}'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
