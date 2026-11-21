# Capstone Grading Rubric – Multi-Agent Creative Studio

Each criterion is scored 25/50/75/100. Final score = weighted average. **Target: Excellent (100) on every row.**

| # | Criterion | Weight |
|---|---|---|
| 1 | Multi-Agent Orchestration & Workflow | 20% |
| 2 | Quality Gate & Revision Loop | 20% |
| 3 | A2A Communication & Distributed Architecture | 15% |
| 4 | Multimodal Image Generation (Designer) | 15% |
| 5 | ADK Skills & MCP Integration | 10% |
| 6 | Reliability & Verification | 10% |
| 7 | Deployment, Code Quality & Documentation | 10% |

## 1. Multi-Agent Orchestration & Workflow (20%)

- **Excellent (100):** The Creative Director orchestrates all six agents in the correct order (Brand Strategist → Copywriter → Designer → Critic → Project Manager). Each specialist is wrapped as a tool and invoked using the agent-as-tool pattern. Context is carried forward automatically so every agent receives the upstream output as structured input. The orchestrator enforces the Critic’s verdict before advancing to planning, and assembles research, copy, images, review, and timeline into one coherent campaign. Generation limits and timeouts are configured on the orchestrator.
- **Good (75):** The orchestrator runs all specialists in the right order and produces a complete campaign. Context mostly flows correctly, with minor gaps (e.g., one specialist receives less context than ideal). The pipeline completes but a step may occasionally need a manual nudge.
- **Satisfactory (50):** A basic pipeline exists and calls most specialists, but ordering or context hand-off has notable gaps. Some outputs are not passed downstream. The final campaign is incomplete or only loosely assembled.
- **Needs Improvement (25):** No working orchestrator, or specialists are called ad hoc without a coherent pipeline. Context is not carried forward. The system does not produce an end-to-end campaign.

## 2. Quality Gate & Revision Loop (20%)

- **Excellent (100):** The Critic returns a strictly structured verdict (POSTS REVIEW / VISUALS REVIEW / OVERALL ASSESSMENT) with 1-10 scores and an APPROVED or NEEDS_REVISION status. It calls the image-review tool for each generated image rather than scoring from text. The orchestrator parses the verdict programmatically and, on NEEDS_REVISION, re-runs the Copywriter or Designer with the feedback, then re-reviews - looping until approved. Only an approved campaign advances to planning.
- **Good (75):** The Critic produces a structured verdict and the revision loop works, with minor issues (e.g., the loop occasionally re-runs the wrong specialist, or the format wobbles slightly but still parses correctly).
- **Satisfactory (50):** The Critic reviews copy and visuals but the output format is inconsistent, or the revision loop is only partial (it flags problems but does not reliably re-run and re-check). Images may be scored from text without being inspected.
- **Needs Improvement (25):** No quality gate, or the Critic’s output cannot be parsed by the orchestrator. No revision loop - work proceeds to planning regardless of quality.

## 3. A2A Communication & Distributed Architecture (15%)

- **Excellent (100):** All five specialists run as independent A2A services, each publishing a valid agent card at its well-known address. The Creative Director wraps each remote specialist as a RemoteA2aAgent tool by reading its URL from the environment - no specialist logic is duplicated in the orchestrator. Agents are reachable and inspectable (e.g., via the A2A Inspector), and each can be updated or scaled independently.
- **Good (75):** Specialists run as independent services with working agent cards and are called over A2A. Minor issues such as one URL hardcoded or an agent card with incomplete metadata.
- **Satisfactory (50):** Some agents run remotely but others are embedded in the orchestrator, or the A2A wiring is fragile (e.g., URLs are not configurable). Communication works only in part.
- **Needs Improvement (25):** No real A2A communication. Specialist logic is copied into the orchestrator, or agents cannot call one another. The system is a single monolith rather than distributed services.

## 4. Multimodal Image Generation (Designer) (15%)

- **Excellent (100):** The Designer exposes an image-generation tool that calls a Gemini image model, uploads each result to Cloud Storage, and returns a gcs_uri (not raw bytes). Aspect ratio (1:1 / 4:5) is handled by injecting the format into the prompt. At least one real image is generated per caption concept, and later agents can fetch the stored images. The image model is called directly from the tool, not wired in as an agent.
- **Good (75):** Real images are generated and stored, with links returned. Minor issues such as inconsistent aspect-ratio handling or occasional generation retries being required.
- **Satisfactory (50):** Image generation works sometimes but is unreliable, returns raw bytes instead of links, or stores images inconsistently. Not every concept receives a visual.
- **Needs Improvement (25):** No working image generation, or the Designer returns text descriptions instead of real images. Images are not stored or cannot be reached by other agents.

## 5. ADK Skills & MCP Integration (10%)

- **Excellent (100):** The Copywriter loads the instagram-copywriting ADK Skill and uses its caption formulas and platform rules to produce three distinct, on-brand captions. The Project Manager builds a complete timeline and, when configured, syncs the project and tasks to Notion through an MCP toolset; an after_tool_callback rewrites Notion errors into recovery hints. Both the Skill and MCP are used as designed, not bypassed.
- **Good (75):** The Skill is loaded and the captions clearly reflect it; the MCP/Notion sync works with minor issues (e.g., an occasional error not fully recovered). The Project Manager always returns a timeline.
- **Satisfactory (50):** The Skill is referenced but underused, or MCP is wired but unreliable. A timeline is produced, but the Notion integration is incomplete or errors are not handled gracefully.
- **Needs Improvement (25):** No Skill usage (caption rules are hardcoded in the prompt) and no MCP integration. Notion failures crash the Project Manager, or no timeline is produced.

## 6. Reliability & Verification (10%)

- **Excellent (100):** Model and tool calls are wrapped with automatic retries and backoff, and the orchestrator is given more attempts than the specialists. Transient timeouts and quota errors are absorbed without failing the run. Each agent is verified hands-on (Strategist sections present, Designer images landing in the bucket, Critic returning both verdict types, the revision loop firing at least once). Agent scope is respected - e.g., the Strategist stays research-only and includes the current year in searches.
- **Good (75):** Retries are present on most calls and the system is mostly resilient. Verification covers the main agents, with a few edge cases left unchecked.
- **Satisfactory (50):** Some retry handling exists but is inconsistent; transient errors sometimes break a run. Verification of agents is ad hoc.
- **Needs Improvement (25):** No retry or backoff - a single transient error sinks the run. No evidence the agents were verified. Agents drift out of scope (e.g., the Strategist writes copy).

## 7. Deployment, Code Quality & Documentation (10%)

- **Excellent (100):** A working Dockerfile per specialist; deploy scripts push the five specialists to Cloud Run and the Creative Director to Agent Engine, wiring service URLs back into config automatically. Behavior is fully driven by environment variables (text model, image model, region, bucket) with secrets in Secret Manager. Code is modular and organized by agent. A clear README covers setup, local and deployed runs, verification, and teardown, and a short demo of a full campaign run (ideally showing the revision loop) is provided.
- **Good (75):** Deployment works end-to-end and config is environment-driven. Code is organized and the README covers the essentials. Minor gaps (e.g., demo not included, or one value hardcoded).
- **Satisfactory (50):** Deployment partly works or needs manual steps. Some config is hardcoded. The README is minimal and code organization is uneven.
- **Needs Improvement (25):** Deployment does not work or is undocumented. Secrets or model names are hardcoded. No meaningful README; the code is disorganized.
