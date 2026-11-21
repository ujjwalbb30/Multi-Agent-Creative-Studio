# Plan: rubric → milestone → files, schedule, accounts

Final score = weighted average of 7 criteria, each scored 25/50/75/100. Tick boxes as each "Excellent" requirement is *proven* (with evidence in the PR).

> Quality Gate (20%) and Orchestration (20%) are 40% of the grade and are only proven in M6. Plan around a **visible revision loop**, not around finishing agents in isolation.

## 1. Rubric → Milestone → Files

### 2. Quality Gate & Revision Loop (20%) — M4, proven in M6
Files: `critic/agent.py`, `critic/image_review_tool.py`, `creative_director/agent.py`
- [ ] Exact POSTS REVIEW / VISUALS REVIEW / OVERALL ASSESSMENT format, 1-10 scores, APPROVED or NEEDS_REVISION
- [ ] `review_image` called once per image, results used as-is
- [ ] Orchestrator parses the verdict, re-runs Copywriter or Designer, re-reviews until approved
- [ ] Only an approved campaign reaches the Project Manager

### 1. Orchestration & Workflow (20%) — M6
Files: `creative_director/agent.py`
- [ ] Order is Strategist → Copywriter → Designer → Critic → PM
- [ ] Each specialist is wrapped as a tool (agent-as-tool)
- [ ] Upstream output is passed forward as structured input
- [ ] Final campaign contains research, captions, images, review and timeline
- [ ] Generation limits and timeouts set on the orchestrator (easy to miss)

### 3. A2A & Distributed Architecture (15%) — M6 (each agent exposes its card from M2 on)
Files: `creative_director/agent.py`, each specialist's A2A bootstrap, `deploy/deploy_all_specialists.py`, `.env` (`*_AGENT_URL`)
- [ ] Five independent services with valid agent cards
- [ ] `RemoteA2aAgent` URLs come from env vars, none hardcoded
- [ ] No specialist logic copied into the orchestrator
- [ ] Cards inspected with the A2A Inspector (keep a screenshot)

### 4. Multimodal Image Generation (15%) — M3
Files: `designer/image_gen_tool.py`, `designer/agent.py`
- [ ] Direct genai client call (`vertexai=True`), not an agent
- [ ] 1:1 / 4:5 injected into the prompt text
- [ ] Upload to GCS and return `gcs_uri`, never bytes
- [ ] At least one image per caption concept

### 5. Skills & MCP (10%) — M2 (Skill), M5 (MCP)
Files: `copywriter/agent.py` + the `instagram-copywriting` skill folder, `project_manager/agent.py`
- [ ] Skill loaded via `SkillToolset` and visibly used
- [ ] `create_project_manager_agent()` has a no-Notion branch and a Notion-MCP branch
- [ ] `handle_notion_error` after-tool callback rewrites 400/404 errors into hints
- [ ] A text timeline is always returned

### 6. Reliability & Verification (10%) — spread across M2-M6
Files: every agent file, `designer/image_gen_tool.py`, `critic/image_review_tool.py`
- [ ] Retries with backoff on model and tool calls
- [ ] Orchestrator gets more retry attempts than specialists
- [ ] Strategist is research-only and includes the current year in searches
- [ ] Written evidence per agent: sections present, images in the bucket, both Critic verdicts, a revision loop that fires

### 7. Deployment, Code Quality & Docs (10%) — M1 (env), M6 (deploy)
Files: Dockerfile per specialist, `deploy/*.py`, `deploy/teardown_gcp.sh`, `.env.example`, `README.md`, demo
- [ ] Deploy scripts work end-to-end and write URLs and `AGENT_ENGINE_ID` back to `.env`
- [ ] Text model, image model, region and bucket all come from env vars
- [ ] Secrets in Secret Manager
- [ ] README covers setup, local and deployed runs, verification and teardown
- [ ] Demo shows the revision loop
- [ ] M6 cleanup: remove the remaining `os.getenv("GEMINI_MODEL", "gemini-2.5-flash")` fallbacks
  (fail with a RuntimeError like the Strategist and Copywriter do since M2). Still present in
  `agents/critic/agent.py`, `agents/designer/agent.py`, `agents/project_manager/agent.py`,
  `agents/creative_director/agent.py`, `deploy/deploy_all_specialists.py`,
  `deploy/deploy_orchestrator.py`, `deploy/env_utils.py`.
- [ ] M6 reliability: decide whether to retry a whole specialist turn when the model returns
  `TOO_MANY_TOOL_CALLS` (seen on the Strategist in M2). `HttpRetryOptions` only retries HTTP
  errors, so this needs its own policy; it belongs with the orchestrator's retry settings.
  Today the Strategist's `after_model_callback` only turns the empty answer into a visible
  "Research failed" message.
- [ ] M6 teardown: `deploy/teardown_gcp.sh` builds the images bucket name from the project ID
  (`<project>-campaign-images`) instead of reading `GCS_IMAGES_BUCKET`.

**Answered from the repo:** every agent folder has a `retry.py` (retry config lives there), and the skill is at `workshop/starter/agents/copywriter/skills/instagram-copywriting/` (contains `SKILL.md`, `assets/`, `references/`). The numbered `# TODO` comments in `workshop/starter/` are the work list.

## 2. Four-week schedule (starts Fri Nov 20)

| Week | Dates | Milestones | Goal and exit proof |
|---|---|---|---|
| 1 | Nov 21-27 | M1 + M2 | GCP set up, ADK dev UI running. Strategist returns four labelled sections with the current year. Copywriter returns three captions via the Skill. Proof: dev UI screenshots. |
| 2 | Nov 28-Dec 4 | M3 + M4 | Designer's images in the bucket with `gcs_uri`. Critic returns both APPROVED and NEEDS_REVISION on a strong and a weak sample. Proof: bucket listing and two Critic outputs. |
| 3 | Dec 5-11 | M5 + start M6 | PM works with and without Notion; a forced Notion error is caught. Orchestrator runs locally. Specialists deployed to Cloud Run, agent cards inspected. |
| 4 | Dec 12-18 | Finish M6 | Orchestrator on Agent Engine. `run_campaign.py` end-to-end with revision loop firing. README, `.env.example`, demo recording, teardown test. |

Notes:
- The Milestones doc allots about 10 working days; this is stretched to 4 weeks. Week 4 doubles as slack. Don't spend it early.
- Thanksgiving is in Week 1. Do M1 first; don't let M2 slip past Nov 29.
- Week 3: don't wire the orchestrator until every specialist works alone.

## 3. Accounts and keys

Needed now (M1):
- Google Cloud account with billing enabled. Note project ID and project number.
- Enable five APIs: Vertex AI, Cloud Run, Cloud Storage, Secret Manager, Artifact Registry.
- Application Default Credentials: `gcloud auth application-default login`. A login, not a key file. Never create or commit a service-account key.
- A globally unique GCS bucket name for `GCS_IMAGES_BUCKET` (confirm whether the setup scripts create it).
- GitHub account for the public submission repo (fork done).
- Local tools: Python 3.11 or 3.12, uv, gcloud CLI.

Not needed: a separate Gemini/AI Studio key (Vertex AI uses ADC), web-search keys, A2A Inspector keys.

Defer to Week 3 (optional): Notion account, integration token, shared database. Store the token in Secret Manager; use `NOTION_TOKEN=<placeholder>` in `.env.example`.

Two M1 settings to defend in the demo:
- `GOOGLE_CLOUD_LOCATION=global` is for the model endpoint (unlocks preview image models).
- `CLOUD_RUN_REGION` must be a real region like `us-central1`; Cloud Run rejects `global`.
