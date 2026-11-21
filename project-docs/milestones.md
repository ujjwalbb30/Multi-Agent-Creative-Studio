# Project Milestones & FAQ (from course guide)

This document breaks your capstone into six sequential milestones. Each milestone builds on the previous one, so complete them in order. After the milestones, you will find a comprehensive FAQ section covering architecture decisions, deployment tips, edge cases, and submission questions.

**💡 Recommended Approach**

Do not try to build everything at once. Follow the milestones in sequence. Get each agent working and verified before moving to the next. The most common mistake learners make is wiring the orchestrator before the individual specialists actually work on their own.

# Part 1: Core Milestones

Six milestones, each with clear tasks, a deliverable, and a self-check list so you know when you are ready to move on.

## Milestone 1 — Environment & Cloud Foundation

*Suggested Duration: Day 1-2*

Before any agent can run, you need a working cloud environment and the starter project running locally. In this milestone you set up your Google Cloud project, install the tooling, configure your environment file, and confirm the ADK dev UI launches.

**What To Do**

• Create a Google Cloud project with billing enabled and enable the required APIs: Vertex AI, Cloud Run, Cloud Storage, Secret Manager, and Artifact Registry.

• Install **uv** (the dependency manager) and authenticate the gcloud CLI for Application Default Credentials with gcloud auth application-default login.

• Clone the repository and work from workshop/starter/. Run uv sync to install dependencies into a managed virtual environment.

• Copy .env.example to .env and fill in GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_PROJECT_NUMBER, CLOUD_RUN_REGION (a real region such as us-central1, never global), and GCS_IMAGES_BUCKET.

• Confirm the model settings: GOOGLE_CLOUD_LOCATION set to global (to unlock preview models), GEMINI_MODEL for text, and GEMINI_IMAGE_MODEL for the Designer.

• Launch the ADK dev UI with uv run adk web agents and confirm it loads, even though the agents are still stubs with TODOs.

**Deliverable**

A configured Google Cloud project, a working .env, and the ADK dev UI launching locally against the starter code.

**Self-Check Before Moving On**

**✓** gcloud config get-value project shows your project and billing is enabled.

**✓** All five required APIs are enabled (Vertex AI, Cloud Run, Cloud Storage, Secret Manager, Artifact Registry).

**✓** uv sync completes without errors and uv run adk web agents starts the dev UI.

**✓** Your .env has a real CLOUD_RUN_REGION (not global) and a valid GCS_IMAGES_BUCKET name.

**✓** gcloud auth application-default login has been run and credentials resolve.

## Milestone 2 — Brand Strategist & Copywriter

*Suggested Duration: Day 2-4*

With the environment ready, build the first two specialists. The Brand Strategist gathers research; the Copywriter turns that research into captions, loading a reusable ADK Skill. Together they are the creative foundation everything else builds on.

**What To Do**

• Complete the Brand Strategist (agents/brand_strategist/agent.py): write a research-only system instruction and add the google_search tool. It must return four labelled sections - Audience Insights, Competitive Analysis, Trending Topics, and Key Strategic Insights.

• Keep the Strategist strictly research-only: its instruction must forbid writing captions or designs, and always include the current year in search queries.

• Complete the Copywriter (agents/copywriter/agent.py): write a system instruction that produces three caption variations in distinct tones, each with a hook, 5-10 hashtags, and a clear call to action.

• Load the instagram-copywriting ADK Skill into the Copywriter as a SkillToolset so it can pull in caption formulas and platform rules on demand.

• Instruct the Copywriter to read the Brand Strategist’s output from the conversation and use it as the creative foundation, not start from a blank page.

• Run each agent in the ADK dev UI and confirm its output shape before moving on.

**Deliverable**

A working Brand Strategist that returns structured research and a Copywriter that produces three on-brand captions using the ADK Skill.

**Self-Check Before Moving On**

**✓** The Brand Strategist returns all four labelled sections and reflects current-year context.

**✓** The Strategist never drifts into writing captions or visuals.

**✓** The Copywriter returns exactly three captions, each in a different tone with hashtags and a CTA.

**✓** The Copywriter loads and uses the instagram-copywriting Skill (not hardcoded rules).

**✓** The captions clearly build on the Strategist’s research when both are run in sequence.

## Milestone 3 — Designer & Multimodal Image Generation

*Suggested Duration: Day 4-5*

Now make the system multimodal. The Designer takes caption concepts and produces real images by calling a Gemini image model, then stores each one in Cloud Storage and returns a link the rest of the team can use.

**What To Do**

• Implement the image-generation tool body in agents/designer/image_gen_tool.py: create a genai client (vertexai=True) and call the image model with the prompt.

• Inject the aspect ratio into the prompt text (1:1 square or 4:5 portrait) - it is not a real API parameter for native image generation.

• Upload each generated image to your GCS bucket and return a structured result containing the gcs_uri (never raw image bytes).

• Register the tool on the Designer agent as a FunctionTool and write a system instruction that turns each concept into a vivid image prompt.

• Remember the image model is separate from the text model and does not support tool-calling, so it is called directly inside the tool, not wired as an agent.

• Generate at least one image per caption concept and confirm the files land in your bucket.

**Deliverable**

A working Designer that generates a real image per concept, stores it in Cloud Storage, and returns a gcs_uri for each.

**Self-Check Before Moving On**

**✓** Running the Designer produces image files that actually appear in your GCS bucket.

**✓** Each result returns a gcs_uri, not raw bytes.

**✓** Square (1:1) and portrait (4:5) requests produce visibly different formats.

**✓** At least one image is generated per caption concept.

**✓** Generation failures (quota or transient) are retried rather than crashing the agent.

## Milestone 4 — Critic & Quality Gate

*Suggested Duration: Day 5-7*

Add the automatic reviewer. The Critic scores the copy and the real images and returns a strictly structured verdict that the orchestrator will parse to decide whether to revise. Its format must be exact.

**What To Do**

• Complete the Critic (agents/critic/agent.py): its system instruction defines a 1-10 rubric and the exact, parseable output (POSTS REVIEW / VISUALS REVIEW / OVERALL ASSESSMENT with APPROVED or NEEDS_REVISION).

• Implement the image-review tool (critic/image_review_tool.py): for each gcs_uri, call Gemini with a forced structured-JSON response and return score, approval_status, what_works, issues, and suggestions.

• Instruct the Critic to call review_image once per image and to use those results directly - never to re-score or guess from text.

• Aggregate across concepts: use the lowest score and mark NEEDS_REVISION if any concept needs revision.

• Handle the no-images case explicitly (NOT_REVIEWED), treating it as approved when computing the overall verdict.

• Test the Critic with a strong and a weak sample to confirm it returns both APPROVED and NEEDS_REVISION in the exact format.

**Deliverable**

A working Critic that inspects real images and returns a strictly structured, parseable APPROVED / NEEDS_REVISION verdict.

**Self-Check Before Moving On**

**✓** The Critic’s output always follows the exact POSTS / VISUALS / OVERALL structure.

**✓** review_image is called once per image and its scores are used as-is.

**✓** A weak campaign returns NEEDS_REVISION with a clear priority fix.

**✓** The no-images case returns NOT_REVIEWED and is treated as approved.

**✓** The OVERALL verdict reflects the lowest concept score across all images.

## Milestone 5 — Project Manager & MCP Integration

*Suggested Duration: Day 7-8*

Build the final specialist. The Project Manager turns the approved campaign into a timeline and task list and, optionally, syncs it to Notion through an MCP toolset - without any custom integration code.

**What To Do**

• Complete the Project Manager (agents/project_manager/agent.py): write a system instruction that produces a campaign timeline and task list from the approved copy and visuals.

• Implement the create_project_manager_agent() branching: a no-Notion branch that returns a text timeline, and a Notion-MCP branch that connects to the Notion MCP server.

• When Notion is configured, create the project and its tasks in the Notion database through the MCP toolset.

• Implement the after_tool_callback (handle_notion_error) that rewrites Notion 400/404 errors into actionable recovery hints (e.g., set the parent with database_id, do not set people properties).

• Always return a complete text timeline even when Notion is not configured - the plan must never depend on the optional integration.

• Store the Notion token and database IDs as secrets at deploy time, never in code.

**Deliverable**

A working Project Manager that always produces a timeline and, when configured, syncs the project and tasks to Notion via MCP with graceful error recovery.

**Self-Check Before Moving On**

**✓** Without Notion configured, the Project Manager still returns a full text timeline.

**✓** With Notion configured, a project and its tasks appear in the Notion database.

**✓** A forced Notion error is caught and turned into a recovery hint, not a crash.

**✓** NOTION_TOKEN and database IDs are read from secrets or environment, never hardcoded.

**✓** The timeline reflects the approved captions and visuals.

## Milestone 6 — Orchestrator, A2A & Deployment

*Suggested Duration: Day 8-10*

Bring it all together. Wire the Creative Director to call each specialist over A2A, deploy the five specialists to Cloud Run and the orchestrator to Agent Engine, and run a full campaign end-to-end - including the revision loop - then prepare for submission.

**What To Do**

• Complete the Creative Director (agents/creative_director/agent.py): for each specialist URL in the environment, create a RemoteA2aAgent pointing at its agent card and wrap it as an AgentTool - never copy specialist logic into the orchestrator.

• Confirm the orchestration prompt runs the pipeline in order and enforces the Critic’s verdict, re-running the Copywriter or Designer on NEEDS_REVISION until approved.

• Deploy the five specialists to Cloud Run with python3 deploy/deploy_all_specialists.py (it writes the *_AGENT_URL values back into .env).

• Deploy the orchestrator to Agent Engine with python3 deploy/deploy_orchestrator.py --action deploy (it writes AGENT_ENGINE_ID back into .env).

• Run an end-to-end campaign with uv run run_campaign.py and confirm research, captions, real images, a review, and a timeline all come back.

• Write a clear README (setup, local and deployed runs, verification, teardown), confirm .env.example is complete, and record a short demo of one full run - ideally showing the revision loop firing.

• Optionally inspect each deployed agent’s card with the A2A Inspector (bash workshop/setup_inspector.sh).

**Deliverable**

A fully deployed, submission-ready system: five specialists on Cloud Run, the orchestrator on Agent Engine, a working end-to-end campaign, a clear README, and a demo.

**Self-Check Before Moving On**

**✓** deploy_all_specialists.py deploys all five services and populates the *_AGENT_URL values.

**✓** deploy_orchestrator.py deploys the Creative Director and writes AGENT_ENGINE_ID to .env.

**✓** uv run run_campaign.py returns a complete campaign for a one-line brief.

**✓** The revision loop fires at least once on a borderline brief and ends in APPROVED.

**✓** No specialist logic is duplicated in the orchestrator - all calls go over A2A.

**✓** README, .env.example, and a short demo of a full run are all present.

**✓** bash deploy/teardown_gcp.sh cleanly removes the provisioned resources.

# Milestone Timeline Summary

Here is the recommended timeline at a glance. Adjust based on your pace, but do not skip milestones or change the order.

| **\#** | **Milestone**                      | **Duration** | **Key Output**                                 |
|--------|------------------------------------|--------------|------------------------------------------------|
| 1      | **Environment & Cloud Foundation** | Day 1-2      | GCP project, .env, ADK dev UI running          |
| 2      | **Brand Strategist & Copywriter**  | Day 2-4      | Research agent + Copywriter with ADK Skill     |
| 3      | **Designer & Image Generation**    | Day 4-5      | Real images in Cloud Storage, gcs_uri returned |
| 4      | **Critic & Quality Gate**          | Day 5-7      | Structured APPROVED / NEEDS_REVISION verdict   |
| 5      | **Project Manager & MCP**          | Day 7-8      | Timeline + optional Notion sync via MCP        |
| 6      | **Orchestrator, A2A & Deployment** | Day 8-10     | Deployed system, end-to-end campaign, README   |

# Part 2: Frequently Asked Questions

## Architecture & Design Decisions

**Q: Why do we need six agents instead of one?**

> One agent trying to research, write, design, review, and plan would have an enormous prompt and too many tools, and it would constantly mix up roles. Splitting the work into focused specialists - each with one job, its own instruction, and a small toolset - dramatically improves quality and makes the system far easier to debug and extend. The Creative Director coordinates them so the user still sees one simple interface.

**Q: What is the Creative Director and why does it exist?**

> The Creative Director is the orchestrator - the only agent the user talks to. It calls each specialist in order, carries each one’s output forward as context for the next, enforces the Critic’s quality gate, and assembles the final campaign. Without it, the user would have to drive each specialist by hand and stitch the results together.

**Q: What does “agent as a tool” mean?**

> The orchestrator does not contain the specialists’ logic. It treats each remote specialist like a callable tool: send the brief to the Brand Strategist, wait for the answer, pass it to the Copywriter, and so on. Each specialist is wrapped as a RemoteA2aAgent and exposed to the orchestrator as an AgentTool. This is the core mental model for the whole project.

**Q: What is Google ADK and why are we using it?**

> ADK (Agent Development Kit) is Google’s framework for building agents. It defines agents, tools, callbacks, and Skills, and it provides the A2A server bootstrap that lets each specialist run as an independent service. Using ADK means you focus on agent logic rather than wiring infrastructure by hand.

## Agents & ADK Skills

**Q: What does each specialist do?**

> The Brand Strategist researches the audience, competitors, and trends. The Copywriter writes three caption variations. The Designer generates real images. The Critic reviews copy and visuals. The Project Manager builds a timeline and optionally syncs tasks to Notion. The Creative Director orchestrates all five.

**Q: What is an ADK Skill?**

> A Skill is a bundle of reference knowledge - plain-text files of formulas, rules, and examples - that an agent loads on demand. The Copywriter loads the instagram-copywriting Skill instead of cramming every caption rule into its prompt. It is like handing a writer a style guide rather than reciting every rule out loud.

**Q: Why should the Brand Strategist stay research-only?**

> Scope discipline keeps the pipeline clean. If the Strategist also wrote captions, its output would blur with the Copywriter’s job and the orchestrator would have a harder time passing clean context downstream. Each agent doing exactly one thing is what makes the system reliable.

**Q: Why include the current year in the Strategist’s search queries?**

> Trends and competitor information go stale quickly. Adding the current year to search queries keeps results fresh and relevant instead of surfacing outdated campaigns or last year’s trends.

## Multimodal & Image Generation

**Q: How does the Designer actually create images?**

> Its tool calls a Gemini image model directly with the image prompt, receives the generated image, uploads it to a Cloud Storage bucket, and returns a gcs_uri. The agent never passes raw bytes around in its response - it works with the storage link.

**Q: Why return a gcs_uri instead of the image itself?**

> Returning raw bytes would bloat every response and make it hard for later agents to reference the image cleanly. A gcs_uri is a small, stable pointer: the Critic can fetch the same image to review it, and the orchestrator can mint a short-lived signed URL to display it.

**Q: How is aspect ratio handled?**

> Native Gemini image generation does not take aspect ratio as an API parameter. Instead, you describe the desired format inside the prompt (for example, “square 1:1” or “portrait 4:5”), and the model honors it. The Designer’s tool injects this hint automatically.

**Q: Why is the image model called directly instead of as an agent?**

> The image model does not support tool-calling, so it cannot act as an ADK agent. The correct pattern is to call it directly from inside the Designer’s FunctionTool, while the Designer (a text agent) decides what to generate.

## Quality Gate & Revision Loop

**Q: Why must the Critic’s output format be so strict?**

> The orchestrator parses the Critic’s verdict programmatically to decide whether to loop back for revisions. If the wording or structure drifts, the orchestrator cannot read it and the revision loop breaks. Designing outputs that other software depends on is a key lesson here.

**Q: How does the revision loop work?**

> When the Critic returns NEEDS_REVISION, the orchestrator re-runs the Copywriter or Designer with the Critic’s feedback, then sends the revised work back to the Critic. It repeats until the verdict is APPROVED, and only then advances to the Project Manager - all without a human in the loop.

**Q: Does the Critic really look at the images?**

> Yes. For each gcs_uri, it calls an image-review tool that sends the actual image to Gemini for a structured assessment, rather than guessing from the captions. The Critic uses those returned scores directly instead of inventing its own.

**Q: What happens if no images are provided?**

> The Critic writes the visuals section as NOT_REVIEWED and treats that as approved when computing the overall verdict, so a copy-only run is not blocked by a missing visual review.

## MCP & Project Manager

**Q: What is MCP?**

> The Model Context Protocol (MCP) is a standard way to connect an agent to an external service without custom integration code. Here it lets the Project Manager read and write a Notion database by pointing at a ready-made Notion MCP server. Think of MCP as a universal adapter between agents and tools.

**Q: Is Notion required?**

> No. Notion is optional. Without it, the Project Manager still returns a complete text timeline; it simply does not sync tasks to a database. The plan must never depend on the optional integration.

**Q: How are Notion errors handled?**

> An after_tool_callback (handle_notion_error) intercepts failed Notion calls and rewrites 400/404 errors into actionable hints - for example, set the parent with database_id, do not set people properties, and confirm the integration is shared with the database. The text timeline is produced regardless.

**Q: Where do the Notion secrets live?**

> At deploy time, the Notion token and database IDs are written to Secret Manager and injected into the Project Manager. They are never committed to the repository; only .env.example (with placeholders) is tracked.

## Deployment & Cloud

**Q: Why are the specialists on Cloud Run but the orchestrator on Agent Engine?**

> Each specialist is a stateless HTTPS service, which is exactly what Cloud Run hosts well - autoscaling containers on a port. The Creative Director is a managed reasoning engine, so it runs on Vertex AI Agent Engine. The deploy scripts handle both and wire the URLs together.

**Q: Why must CLOUD_RUN_REGION be a real region, not global?**

> global is only valid for the Vertex AI model endpoint (GOOGLE_CLOUD_LOCATION), which uses it to reach preview models. Cloud Run and Agent Engine need an actual region such as us-central1. Setting the Cloud Run region to global will fail the deploy.

**Q: How does the orchestrator find the specialists after deployment?**

> The specialist deploy script writes each service’s URL back into .env (COPYWRITER_AGENT_URL and so on). The Creative Director reads those URLs at runtime and points each RemoteA2aAgent at the matching agent card.

**Q: Why does specialist deployment run sequentially?**

> Cloud Build polling is rate-limited, so the script deploys the five specialists one at a time by design. If you still hit the limit, wait a minute and re-run the deploy script.

## Reliability & Verification

**Q: How does the system survive transient failures?**

> Model and tool calls are wrapped with automatic retries and backoff. The orchestrator is given more attempts than the specialists, since it depends on all of them. A brief timeout or quota error is absorbed instead of sinking the whole campaign run.

**Q: There is no automated test suite - how do I verify my work?**

> This project is a teaching scaffold without automated tests, so verification is hands-on. Run each specialist in the ADK dev UI, confirm the Designer’s images land in your bucket, feed the Critic strong and weak samples, and trace one full run to watch the revision loop fire. The self-check lists in each milestone are your checklist.

**Q: How do I confirm A2A is actually working?**

> After deployment, inspect each agent’s card with the A2A Inspector (bash workshop/setup_inspector.sh). A reachable, well-formed agent card means the orchestrator can call that specialist remotely.

## Submission & Common Issues

**Q: What should my GitHub repo include?**

> All six agents implemented under agents/ (the five specialists plus the Creative Director), the deploy scripts under deploy/, a Dockerfile per specialist, a complete .env.example, and a README explaining setup, local and deployed runs, verification, and teardown. A short demo of a full campaign is strongly recommended.

**Q: What should I NOT commit?**

> Your real .env file, API keys, and service-account key files; the Notion token or database IDs in plain text; and any hardcoded secrets anywhere in the code. Secrets belong in Secret Manager.

**Q: My deploy stalls on Cloud Build polling. What should I check?**

> This is the rate limit. The script deploys specialists sequentially on purpose; if you still hit it, wait a minute and re-run deploy_all_specialists.py. Confirm billing is enabled and the required APIs are on.

**Q: Image generation fails or returns no bytes. What now?**

> Confirm GEMINI_IMAGE_MODEL is available in your GOOGLE_CLOUD_LOCATION (use global for preview models), and that GCS_IMAGES_BUCKET exists and is writable. Remember the image model does not support function calling, so it must be called directly from the Designer’s tool. The Designer retries on quota errors.

**Q: The orchestrator gets a 403 or cannot reach a specialist. What’s wrong?**

> Make sure each service deployed with --allow-unauthenticated, the \*\_AGENT_URL values are populated in .env, and the deploy set the A2A host config (PUBLIC_HOST, PUBLIC_PORT=443, PROTOCOL=https). A missing or wrong URL is the usual cause.

**Q: run_campaign.py cannot find the engine. How do I fix it?**

> AGENT_ENGINE_ID is empty. Deploy the orchestrator first with deploy_orchestrator.py --action deploy, which writes the ID back into .env, then run the campaign again.

**You have everything you need. Start building!**

*Follow the milestones, check yourself at each step, and refer to the FAQ when stuck.*
