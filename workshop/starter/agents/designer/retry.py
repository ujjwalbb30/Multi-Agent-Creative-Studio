"""Gemini retry config for handling 429 rate limits and transient errors.

Pay-as-you-go uses Dynamic Shared Quota, so 429s can fire when the shared pool
is busy. Application-level retries are required (the SDK does not enable them
by default). Pass `generate_content_config=GENERATE_CONTENT_CONFIG` to every
Agent so each model call retries with exponential backoff before giving up.
"""

from google.genai import types

RETRY_CONFIG = types.HttpRetryOptions(
    attempts=3,
    exp_base=2,
    initial_delay=5,
    http_status_codes=[429, 500, 503, 504],
)

GENERATE_CONTENT_CONFIG = types.GenerateContentConfig(
    http_options=types.HttpOptions(
        retry_options=RETRY_CONFIG,
        timeout=120_000,  # 120 second timeout for model calls
    ),
)

# Image generation quota (DSQ) recovers slowly, so the image model gets more
# attempts and a longer first wait than the text model. The SDK caps each wait at
# max_delay (60 s by default), so the waits are about 30, 60, 60, 60 s.
IMAGE_RETRY_CONFIG = types.HttpRetryOptions(
    attempts=5,
    exp_base=2,
    initial_delay=30,
    http_status_codes=[429, 500, 503, 504],
)

IMAGE_GENERATE_CONFIG = types.GenerateContentConfig(
    response_modalities=["IMAGE", "TEXT"],
    http_options=types.HttpOptions(
        retry_options=IMAGE_RETRY_CONFIG,
        timeout=180_000,  # 180 second timeout per image call
    ),
)
