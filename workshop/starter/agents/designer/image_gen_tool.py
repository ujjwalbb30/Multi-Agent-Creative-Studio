"""Gemini native image generation tool. Generates image, uploads to GCS, returns URI."""
import asyncio
import io
import os
import re
import uuid

from google import genai
from google.adk.tools import ToolContext
from google.cloud import storage
from google.genai import types
try:
    from .retry import IMAGE_GENERATE_CONFIG
except ImportError:
    from retry import IMAGE_GENERATE_CONFIG

ASPECT_HINTS = {
    "1:1": "square 1:1 aspect ratio (1080x1080)",
    "4:5": "portrait 4:5 aspect ratio (1080x1350)",
}

# Obvious ways a model may phrase the two Instagram formats.
ASPECT_ALIASES = {
    "1:1": "1:1", "square": "1:1", "1080x1080": "1:1",
    "4:5": "4:5", "portrait": "4:5", "1080x1350": "4:5",
}


def normalize_aspect_ratio(aspect_ratio: str) -> str | None:
    """Maps variants like 'Square', '1080 x 1350' or '4/5' to '1:1' / '4:5'."""
    key = re.sub(r"\s+", "", (aspect_ratio or "").lower()).replace("×", "x").replace("/", ":")
    return ASPECT_ALIASES.get(key)


def safe_concept_name(concept_name: str) -> str:
    """Keeps the model-supplied name safe to use inside a GCS object path."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", concept_name or "").strip("-")
    return cleaned[:60] or "concept"


async def generate_image(
    concept_name: str,
    image_prompt: str,
    aspect_ratio: str,
    tool_context: ToolContext,
) -> dict:
    """
    Generate an image with Gemini native image generation and upload it to GCS.

    Args:
        concept_name: Short identifier for this image concept (e.g. "post1_concept_a")
        image_prompt: Full image generation prompt string
        aspect_ratio: "1:1" for square (1080x1080) or "4:5" for portrait (1080x1350)

    Returns:
        {"status": "success", "gcs_uri": "gs://...", "concept_name": "..."}
        or {"status": "error", "error": "..."}
    """
    bucket_name = os.environ.get("GCS_IMAGES_BUCKET")
    if not bucket_name:
        return {"status": "error", "error": "GCS_IMAGES_BUCKET env var not set"}

    # Use a dedicated image-capable model; separate from the text GEMINI_MODEL.
    # It does NOT support function calling, so it cannot be used as an ADK agent -
    # but calling it directly here is fine.
    image_model = os.environ.get("GEMINI_IMAGE_MODEL")
    if not image_model:
        return {"status": "error", "error": "GEMINI_IMAGE_MODEL env var not set"}

    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")

    ratio = normalize_aspect_ratio(aspect_ratio)
    if ratio is None:
        return {
            "status": "error",
            "error": f"Unsupported aspect_ratio {aspect_ratio!r}; use '1:1' (square) or '4:5' (portrait)",
        }
    concept_name = safe_concept_name(concept_name)

    # Aspect ratio is not an API parameter for Gemini native generation - inject it
    # into the prompt so the model respects the desired Instagram format.
    prompt_with_aspect = f"{image_prompt}\n\nGenerate this as a {ASPECT_HINTS[ratio]} image."

    try:
        # Async client so a slow call or a retry backoff does not block the event
        # loop (and every other request this service is handling).
        client = genai.Client(vertexai=True, project=project_id, location=location)
        response = await client.aio.models.generate_content(
            model=image_model,
            contents=prompt_with_aspect,
            config=IMAGE_GENERATE_CONFIG,
        )

        image_bytes = None
        mime_type = "image/png"
        candidate = response.candidates[0] if response.candidates else None
        parts = candidate.content.parts if candidate and candidate.content and candidate.content.parts else []
        for part in parts:
            if part.inline_data is not None and part.inline_data.data:
                image_bytes = part.inline_data.data
                mime_type = part.inline_data.mime_type or "image/png"
                break
        if not image_bytes:
            reason = candidate.finish_reason if candidate else "no candidates"
            return {"status": "error", "error": f"Gemini returned no image data (finish_reason={reason})"}

        ext = "jpg" if "jpeg" in mime_type else "png"
        blob_name = f"campaign-images/{concept_name}-{uuid.uuid4().hex[:8]}.{ext}"

        def upload() -> None:
            bucket = storage.Client(project=project_id).bucket(bucket_name)
            bucket.blob(blob_name).upload_from_file(io.BytesIO(image_bytes), content_type=mime_type)

        # The storage client is blocking; run it in a worker thread.
        await asyncio.to_thread(upload)
        gcs_uri = f"gs://{bucket_name}/{blob_name}"

        # Save as ADK artifact so adk web renders the image inline when testing
        # the Designer directly. Silently skipped when no artifact service is
        # configured (e.g. Cloud Run deployment).
        try:
            artifact = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
            await tool_context.save_artifact(f"{concept_name}.{ext}", artifact)
        except ValueError:
            pass

        return {"status": "success", "gcs_uri": gcs_uri, "concept_name": concept_name, "aspect_ratio": ratio}

    except Exception as e:
        # Reached only after the SDK retries are used up, or on a non-retryable
        # error; return it so the agent can report it and continue.
        return {"status": "error", "error": f"{type(e).__name__}: {e}"}
