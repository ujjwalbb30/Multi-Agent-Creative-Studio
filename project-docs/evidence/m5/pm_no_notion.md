# No Notion configured: text timeline only

Case `pm_no_notion`, run 2026-11-26T02:14:12, model `gemini-3-flash-preview`, commit `57cfeaa`.

## Checks

- PASS: run finished without crashing (exit 0)
- PASS: captured stdout/stderr contains no token, bearer header or full database ID
- PASS: final response is not empty (2386 chars)
- PASS: five sections present, in order
- PASS: timeline has Strategy, Creation, Review, Launch in order
- PASS: dates start from today (none in the past, first within 7 days) (23 dates, 2026-11-26 to 2026-12-15)
- PASS: task table has Task | Owner | Deadline | Status and 6+ tasks (11 tasks)
- PASS: tasks name every approved caption
- PASS: plan references the approved visual
- PASS: budget has a total
- PASS: response contains no full IDs or Notion URLs
- PASS: no tools attached, no tool calls
- PASS: Notion Status says not configured (Notion not configured - text timeline only)

## Final response

**Project Timeline:**
- Strategy: 2026-11-26 to 2026-11-27 - Alignment on target Austin remote worker personas and campaign scheduling.
- Creation: 2026-11-28 to 2026-12-02 - Finalizing visual exports and adjusting caption formatting per critic feedback.
- Review: 2026-12-03 to 2026-12-04 - Quality assurance, link checking for "Proffees" info, and final approval for launch.
- Launch: 2026-12-05 to 2026-12-12 - Deployment of posts and active community management.

**Task List:**
| Task | Owner | Deadline | Status |
|---|---|---|---|
| Campaign kickoff and resource allocation | Project Manager | 2026-11-26 | Done |
| Define Austin geo-fencing for ad spend | Brand Strategist | 2026-11-27 | Not Started |
| Apply line breaks and readability tweaks to Captions 1, 2, and 3 | Copywriter | 2026-11-28 | Not Started |
| Export `clean_beans_still_life` for Feed and Story formats | Designer | 2026-11-29 | Not Started |
| Source/Create b-roll for Caption 2 and 3 visual variations | Designer | 2026-12-01 | Not Started |
| Final review of all assets against brand guidelines | Critic | 2026-12-03 | Not Started |
| Schedule all posts in Meta Business Suite | Project Manager | 2026-12-04 | Not Started |
| Publish: The Productivity Pivot (Visual: `clean_beans_still_life`) | Project Manager | 2026-12-05 | Not Started |
| Publish: The "Swavory" Ritual (Visual: `clean_beans_still_life` variant) | Project Manager | 2026-12-08 | Not Started |
| Publish: The Story of the Batch (Visual: `clean_beans_still_life` variant) | Project Manager | 2026-12-12 | Not Started |
| Respond to comments and Austin local tags | Community Manager | 2026-12-13 | Not Started |

**Budget Breakdown:**
- Content Refinement: $350.00 - Final designer hours for formatting `clean_beans_still_life` and minor copy edits.
- Localized Ad Spend: $600.00 - Meta ads targeted at Austin-based remote workers and coffee enthusiasts.
- Community Management: $250.00 - Dedicated time for engagement and 48-hour response windows for the launch week.
- Project Management: $300.00 - Oversight, scheduling, and reporting.
- Total: $1,500.00

**Milestones:**
- 2026-11-26: Campaign Setup Finalized
- 2026-12-03: All Creative Assets Approved for Launch
- 2026-12-05: First Post Live (The Productivity Pivot)
- 2026-12-15: Campaign Performance Report Distributed

**Notion Status:**
Notion not configured - text timeline only
