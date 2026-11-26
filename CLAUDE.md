# Multi-Agent Creative Studio — capstone

Instagram campaign generator: a Creative Director orchestrator plus five specialists
(Brand Strategist, Copywriter, Designer, Critic, Project Manager).
Stack: Python 3.12, uv, Google ADK, A2A, Cloud Run (specialists), Vertex AI Agent Engine
(orchestrator), GCS (images), Notion MCP (optional).

Goal: score "Excellent" on every criterion in `project-docs/rubric.md`.

## Where things are
- Code: `workshop/starter/` (agents in `agents/<name>/`, deploy scripts in `deploy/`,
  `run_campaign.py`, `.env.example`). Run commands from `workshop/starter/`.
- `docs/` is the upstream GitHub Pages codelab. Do not edit, move or delete it.
- Our notes: `project-docs/rubric.md`, `project-docs/milestones.md` (course milestones + FAQ),
  `project-docs/plan.md` (rubric → files checklist and schedule).
- The `# TODO` comments in `workshop/starter/` are the work list.

## How to work
- Do ONE milestone at a time, in order (see `project-docs/milestones.md`). Read only the
  current milestone's section plus the relevant rubric rows. Do not touch other agents.
- Start each milestone by proposing a plan and waiting for approval. Then build in small steps.
- Each specialist must work alone (e.g. in `uv run adk web agents`) before the orchestrator is wired.
- Finish a milestone by running its self-check list and showing the output. Evidence goes in the PR.
- Explain the "why" of each decision briefly and name the rubric criterion it serves.
  The author has to defend this work in a demo.
- Branch per milestone (`m1-environment`, `m2-...`), one PR per branch, small commits.
  Do not merge PRs or push to `main`.

## Hard rules
- Never commit `.env`, API keys, service-account keys, Notion tokens or database IDs.
  Only `.env.example` with placeholders. Secrets belong in Secret Manager at deploy time.
- Never print, cat, grep or otherwise read values from `.env`. To check configuration, report
  only whether each variable is set. Code may load `.env` in-process, but must never output
  its values.
- The Notion token and database IDs must never appear in logs, evidence, test output or PRs.
  Mask any ID to its last 4 characters (e.g. `…a1b2`).
- No hardcoded model names, region, bucket or service URLs. Read them from env vars.
- Ask before running any command that creates or changes cloud resources, billing or
  deployments (gcloud, deploy scripts). Show the command and what it does first.
- Python 3.11 or 3.12 only.

## Design constraints (from the rubric)
- Critic output is strict: POSTS REVIEW / VISUALS REVIEW / OVERALL ASSESSMENT, 1-10 scores,
  APPROVED or NEEDS_REVISION. Do not change the format casually; the orchestrator parses it.
- Critic calls the image-review tool for each image; it never scores images from text.
- Designer calls the Gemini image model directly from its tool (not as an agent), injects
  1:1 / 4:5 into the prompt, uploads to GCS and returns `gcs_uri`, never raw bytes.
- Brand Strategist is research-only (no copywriting) and includes the current year in searches.
- Copywriter loads the `instagram-copywriting` ADK Skill via SkillToolset; no caption rules hardcoded.
- Project Manager always returns a text timeline; Notion MCP is an optional branch, and
  `handle_notion_error` rewrites Notion errors into recovery hints.
- Orchestrator calls specialists over A2A (RemoteA2aAgent, URLs from env). Never copy
  specialist logic into it. Only an approved campaign advances to the Project Manager.
- Retries with backoff on model and tool calls; the orchestrator gets more attempts than specialists
  (each agent folder has a `retry.py`).
- GOOGLE_CLOUD_LOCATION=global (model endpoint); CLOUD_RUN_REGION must be a real region, never global.
