# generate_image called directly, 4:5

Case `tool_portrait`, run 2026-11-21T16:40:20, text model `gemini-3-flash-preview`, image model `gemini-3.1-flash-image`, commit `a146ad6`.

## Checks

- PASS: tool returned status success
- PASS: tool result contains no bytes
- PASS: gcs_uri points into the images bucket (gs://<BUCKET>/campaign-images/tool_portrait-35a00685.png)
- PASS: object exists in the bucket
- PASS: object is a real image (PNG/JPEG header parsed) (image/png, 1864975 bytes)
- PASS: pixel ratio matches 4:5 (±3%) (928x1152 = 0.806)
- PASS: compressed copy under 300 KB (263 KB)

## Images

| # | gcs_uri | ratio asked | pixels | type | seconds | copy |
|---|---|---|---|---|---|---|
| 1 | `gs://<BUCKET>/campaign-images/tool_portrait-35a00685.png` | 4:5 | 928x1152 | image/png | 10.9 | _not committed (third-party logos)_ |

## Prompt

```text
Photorealistic portrait shot of a college student walking across a sunny campus quad holding a matte sage-green reusable water bottle, shallow depth of field, warm golden-hour light.
```
