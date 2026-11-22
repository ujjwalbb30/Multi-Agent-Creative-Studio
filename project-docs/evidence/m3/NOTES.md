## Notes on this evidence

**Model.** `GEMINI_IMAGE_MODEL=gemini-3.1-flash-image` worked on the `global` endpoint on the
first call; no fallback model was needed.

**Aspect ratio.** The ratio is only a hint injected into the prompt (as the rubric asks); no
`ImageConfig` is used. The model returned 1024x1024 for 1:1 and 928x1152 (0.806) for 4:5, so the
shapes are right but the pixel sizes are not exactly Instagram's 1080x1080 / 1080x1350. The check
compares the ratio within ±3%.

**Retries.** A real 429 cannot be triggered on demand, so retry behaviour is proven offline by
`workshop/starter/scripts/test_m3_designer_offline.py`: against a local stub server the SDK retries
429, 429 and then succeeds (3 requests), does not retry a 403 (1 request), and stops after 5
attempts. The stub test uses the Gemini Developer API client with a dummy API key (no Google
token is sent); the retry logic is in the API client shared with the Vertex client. The same file
checks that the tool attaches the image retry config (5 attempts, 30 s first wait) and returns an
error dict instead of raising. No retries happened in these live runs.

**Images.** The JPEG copies in [images/](images/) are compressed versions of the PNGs in the bucket
(under 300 KB each). Two copies were left out of the repo because the model drew what looks like
real third-party branding into them: `tool_portrait` (a bottle brand's logo) and
`designer_sequence` image 1 ("MacBook Pro" on a laptop, other café brand names). Their checks still
ran on the full images in the bucket; their rows say "not committed (third-party logos)". The model
adding real logos to images for a fictional brand is something for the Critic to catch in M4.
None of the images contain project identifiers.

**Designer input.** `designer_sequence` puts the committed M2 evidence in the conversation history:
the grounded `strategist_b` research and the three `sequence_b` captions (Ember & Oak).
