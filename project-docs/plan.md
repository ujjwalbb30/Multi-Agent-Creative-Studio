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
- [ ] M6 (from M4): an unparseable Critic verdict is a failure, not a pass. The orchestrator
  re-asks the Critic once; if it still does not parse, stop and report instead of advancing.
  (The Critic's `verdict.py` guard leaves unparseable text unchanged and logs it.)
- [ ] M6 (from M4): `[REVIEW_ERROR]` in VISUALS Issues or Priority Revisions means the image
  review itself failed (e.g. the Critic could not read the image). Re-ask the Critic; do not
  regenerate images for it.
- [ ] M6 (from M4): cap revision rounds (e.g. `MAX_REVISION_ROUNDS` from env, default 3). When
  the cap is hit, stop with the last verdict and say so; never advance an unapproved campaign.
  The cap also matters because image review scores vary between runs: in M4 the same image
  scored 6 and 8, so a borderline image can flip between pass and fail on an identical retry.
- [ ] M6 (from M3/M4): Designer constraints for revisions. Every M3 image had a flaw the Critic
  rejects. Tell the Designer (instruction or revision prompt): no real logos or brand names other
  than the campaign's own, avoid visible text and hands where possible, and pass the Critic's
  VISUALS Issues and Suggestions into the revision prompt verbatim. The M4 clean test image made
  from such a prompt scored 9, 9 and 10.

### 1. Orchestration & Workflow (20%) — M6
Files: `creative_director/agent.py`
- [ ] Order is Strategist → Copywriter → Designer → Critic → PM
- [ ] Each specialist is wrapped as a tool (agent-as-tool)
- [ ] Upstream output is passed forward as structured input
- [ ] Final campaign contains research, captions, images, review and timeline
- [ ] Generation limits and timeouts set on the orchestrator (easy to miss)
  - Designer image latency (from M3, `designer/retry.py`): each `generate_image` call has a 180 s
    timeout and 5 attempts, and timeouts are retried too. The SDK caps each wait at `max_delay`
    (60 s by default in google-genai 1.75.0), so the waits are about 30, 60, 60, 60 s (~210 s).
    Worst case per image: 5 x 180 s + ~210 s, about 18 minutes. Typical failure with fast 429s:
    ~210 s, about 3.5 minutes. Measured in M3: 10.4-11.5 s per image (5 images, no retries).
    A Designer turn makes one call per caption, in sequence, so 3 captions take ~35 s normally
    and could take ~10 minutes with fast 429s. Size the orchestrator's A2A timeout for the
    Designer with this in mind.

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
- [ ] M6 reliability (decided in M2: retries live in the orchestrator): when the Strategist's
  answer contains `⚠ No Google Search results were used` or `⚠ Research failed (`, the Creative
  Director re-asks the Strategist once before moving on. In M2 the Strategist searched in only
  about half of the ADK runs on the final instruction (see `project-docs/evidence/m2/NOTES.md`),
  and `HttpRetryOptions` cannot catch this because the call itself succeeds (`STOP`) or ends with
  `TOO_MANY_TOOL_CALLS`. The Strategist's `after_model_callback` only makes these cases visible.
- [ ] M6 IAM (from M4): all Cloud Run services run as the default compute service account, and
  the deploy script grants only the Designer `roles/storage.objectCreator` (write-only) on the
  images bucket. Verify on Cloud Run that the Critic can read the images (`Part.from_uri` on
  `gs://`); grant `roles/storage.objectViewer` if needed. Local runs use the developer's own
  credentials, so they prove nothing about this.
- [ ] M6 logging (from M5): check Cloud Run stderr / Cloud Logging for every service after a
  deployed run, including a failed Notion call. notion-mcp-server writes the Authorization header
  (the token) to stderr on HTTP errors; the PM starts it as `sh -c 'exec notion-mcp-server
  2>/dev/null'` to prevent that. Confirm in Cloud Logging that no token, "Bearer" header or full
  database ID appears (search the PM service logs for the token prefix and the last 4 characters
  of each database ID, never the full values).
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
