"""Offline tests for the Designer's image tool. No paid calls, no cloud access.

1. generate_image with a fake Gemini client and a fake GCS client:
   success, no image returned, an exception, bad / variant aspect ratios.
2. SDK retry against a local stub server: 429, 429, then success -> 3 requests;
   a 403 is not retried -> 1 request.

Usage (from workshop/starter/):
  uv run python scripts/test_m3_designer_offline.py
"""

import asyncio
import json
import os
import pathlib
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "agents"))
os.environ.setdefault("GCS_IMAGES_BUCKET", "test-bucket")
os.environ.setdefault("GEMINI_IMAGE_MODEL", "test-image-model")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test-project")
os.environ.setdefault("GEMINI_MODEL", "test-text-model")

from google import genai  # noqa: E402
from google.genai import errors, types  # noqa: E402

from designer import image_gen_tool as tool  # noqa: E402
from designer.retry import IMAGE_GENERATE_CONFIG, IMAGE_RETRY_CONFIG  # noqa: E402

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
FAILURES = []


def expect(name: str, ok: bool, detail: object = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" ({detail})" if detail != "" else ""))
    if not ok:
        FAILURES.append(name)


# ------------------------------------------------------------ fakes for the tool


class FakeModels:
    def __init__(self, behaviour):
        self.behaviour, self.calls = behaviour, []

    async def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        return self.behaviour


class FakeClient:
    last = None

    def __init__(self, behaviour):
        self.aio = type("Aio", (), {})()
        self.aio.models = FakeModels(behaviour)
        FakeClient.last = self


class FakeStorage:
    uploads = []

    def __init__(self, project=None):
        pass

    def bucket(self, name):
        outer = self

        class Blob:
            def __init__(self, blob_name):
                self.blob_name = blob_name

            def upload_from_file(self, fileobj, content_type):
                outer.uploads.append((name, self.blob_name, len(fileobj.read()), content_type))

        return type("Bucket", (), {"blob": lambda _self, n: Blob(n)})()


class FakeToolContext:
    def __init__(self):
        self.artifacts = []

    async def save_artifact(self, filename, artifact):
        self.artifacts.append(filename)


def image_response(data=PNG_BYTES, mime="image/png"):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[
            types.Part(text="Here is your image."),
            types.Part(inline_data=types.Blob(data=data, mime_type=mime)),
        ]),
        finish_reason=types.FinishReason.STOP,
    )])


async def run_tool(behaviour, aspect="1:1", name="caption1_concept_a"):
    tool.genai.Client = lambda **kw: FakeClient(behaviour)
    tool.storage.Client = FakeStorage
    FakeStorage.uploads = []
    ctx = FakeToolContext()
    result = await tool.generate_image(name, "A bottle on a desk", aspect, ctx)
    return result, ctx


async def test_tool() -> None:
    real_client, real_storage = tool.genai.Client, tool.storage.Client
    try:
        result, ctx = await run_tool(image_response())
        call = FakeClient.last.aio.models.calls[0]
        expect("success returns status and gs:// URI",
               result["status"] == "success" and result["gcs_uri"].startswith("gs://test-bucket/campaign-images/"),
               result)
        expect("result has no bytes", all(not isinstance(v, (bytes, bytearray)) for v in result.values()))
        expect("image uploaded once with its content type",
               FakeStorage.uploads and FakeStorage.uploads[0][2] == len(PNG_BYTES)
               and FakeStorage.uploads[0][3] == "image/png", FakeStorage.uploads)
        expect("aspect hint injected into the prompt", "square 1:1 aspect ratio" in call["contents"])
        expect("image model read from GEMINI_IMAGE_MODEL", call["model"] == "test-image-model")
        expect("image retry config attached (5 attempts, 30 s first wait)",
               call["config"] is IMAGE_GENERATE_CONFIG
               and call["config"].http_options.retry_options.attempts == 5
               and call["config"].http_options.retry_options.initial_delay == 30)
        expect("ADK artifact saved for the dev UI", ctx.artifacts == ["caption1_concept_a.png"], ctx.artifacts)

        result, _ = await run_tool(image_response(mime="image/jpeg"), aspect="4:5")
        expect("jpeg gets .jpg and portrait hint",
               result["gcs_uri"].endswith(".jpg")
               and "portrait 4:5" in FakeClient.last.aio.models.calls[0]["contents"], result)

        empty = types.GenerateContentResponse(candidates=[types.Candidate(
            content=types.Content(role="model", parts=[types.Part(text="I can't draw that.")]),
            finish_reason=types.FinishReason.STOP)])
        result, _ = await run_tool(empty)
        expect("no image data returns an error dict", result["status"] == "error"
               and "no image data" in result["error"] and not FakeStorage.uploads, result)

        result, _ = await run_tool(errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}}))
        expect("exception after retries returns an error dict (no crash)",
               result["status"] == "error" and "429" in result["error"], result)

        for variant, want in [("square", "1:1"), ("Square", "1:1"), ("1080x1080", "1:1"), ("1080 x 1080", "1:1"),
                              ("portrait", "4:5"), ("1080X1350", "4:5"), ("4/5", "4:5"), (" 1:1 ", "1:1")]:
            result, _ = await run_tool(image_response(), aspect=variant)
            expect(f"aspect variant {variant!r} -> {want}", result.get("aspect_ratio") == want, result)

        result, _ = await run_tool(image_response(), aspect="16:9")
        expect("unsupported aspect ratio returns an error before any call",
               result["status"] == "error" and "16:9" in result["error"], result)

        result, _ = await run_tool(image_response(), name="Caption 1 / Concept: A?")
        expect("concept name made safe for the object path",
               "/campaign-images/Caption-1-Concept-A-" in result["gcs_uri"], result["gcs_uri"])
    finally:
        tool.genai.Client, tool.storage.Client = real_client, real_storage


# ------------------------------------------------------------ SDK retry stub


class StubHandler(BaseHTTPRequestHandler):
    plan: list[int] = []
    requests = 0

    def do_POST(self):  # noqa: N802
        type(self).requests += 1
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        status = self.plan.pop(0) if self.plan else 200
        if status == 200:
            body = image_response().model_dump(mode="json", by_alias=True, exclude_none=True)
        else:
            body = {"error": {"code": status, "message": "stub", "status": "STUB"}}
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


async def test_sdk_retry() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    # Same attempts and status codes as the Designer's real image retry config, with
    # the waits shortened so the test runs in seconds.
    fast_retry = IMAGE_RETRY_CONFIG.model_copy(update={"initial_delay": 0.05, "max_delay": 0.1, "jitter": 0.01})
    # Uses the Gemini Developer API client with a dummy API key instead of
    # vertexai=True, so no real Google access token is ever sent to the stub. The
    # retry logic lives in the shared BaseApiClient used by both modes, so this
    # proves the same behaviour the Vertex client has in production.
    client = genai.Client(api_key="dummy-key-for-local-stub",
                          http_options=types.HttpOptions(base_url=base_url, retry_options=fast_retry))
    try:
        StubHandler.plan, StubHandler.requests = [429, 429, 200], 0
        response = await client.aio.models.generate_content(model="stub-model", contents="hi")
        got_image = any(p.inline_data for p in response.candidates[0].content.parts)
        expect("SDK retries 429, 429 then succeeds (3 requests)", StubHandler.requests == 3 and got_image,
               f"{StubHandler.requests} requests")

        StubHandler.plan, StubHandler.requests = [403], 0
        try:
            await client.aio.models.generate_content(model="stub-model", contents="hi")
            expect("403 is not retried", False, "no error raised")
        except errors.ClientError as e:
            expect("403 is not retried (1 request, error raised)", StubHandler.requests == 1 and e.code == 403,
                   f"{StubHandler.requests} requests")

        StubHandler.plan, StubHandler.requests = [429] * 6, 0
        try:
            await client.aio.models.generate_content(model="stub-model", contents="hi")
            expect("gives up after 5 attempts", False, "no error raised")
        except errors.ClientError as e:
            expect("gives up after 5 attempts and raises", StubHandler.requests == 5 and e.code == 429,
                   f"{StubHandler.requests} requests")
    finally:
        server.shutdown()


async def main() -> int:
    print("== image tool (fake clients)")
    await test_tool()
    print("== SDK retry (local stub server)")
    await test_sdk_retry()
    print(f"\n{'ALL PASSED' if not FAILURES else f'{len(FAILURES)} FAILED: {FAILURES}'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
