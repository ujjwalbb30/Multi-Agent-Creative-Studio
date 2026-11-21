## Notes on this evidence

**Check change: hashtags in Strategist output (changed after a failure, kept visible on purpose).**
The first version of the check failed the Strategist on *any* `#tag`. In the first runs the
Strategist named existing trends such as `#WaterTok` and `#AustinRemoteWork` as research findings,
which is not writing copy. The check now fails only on a hashtag *block*: a line with 3 or more
hashtags, which is how captions present hashtags. Single trend mentions are listed in the check
detail so a reviewer can judge them. The outputs that triggered the change are in [pre-fix/](pre-fix/).

**Strategist fix after `strategist_b` answered without searching.** In the pre-fix run of
`strategist_b` no Google Search queries were recorded, and the answer cited a source that was
probably invented (see [pre-fix/strategist_b.md](pre-fix/strategist_b.md)). Commit `b9b0855`
made the instruction require a search for each research area, forbid citing anything not
obtained from search, and added an `after_model_callback` that appends a visible warning when a
final answer has no grounding metadata. The check "answer is grounded" catches that warning.

**Strategist fix after an empty answer (`TOO_MANY_TOOL_CALLS`).** With the per-area search rule,
one `strategist_caption` run returned no text at all. The event diagnostics (added in `95790e2`)
show `finish_reason = TOO_MANY_TOOL_CALLS`: Vertex AI stopped the turn because the model tried
too many searches (see [pre-fix/strategist_caption_too_many_tool_calls.md](pre-fix/strategist_caption_too_many_tool_calls.md)).
Commit `4b45aee` caps searches at 2-3 per area (9 in total) and makes the callback replace an
empty error response with a visible `Research failed (<error_code>)` message. Automatic retry of
a whole turn is deferred to M6 (see `project-docs/plan.md`).

**Known gap: the Strategist does not always search (accepted for M2, fixed in M6).**
On the final instruction (`4b45aee`), the ADK agent searched in 3 of 5 runs. Brief A came back
ungrounded twice in a row (a third, final run for the record searched 6 times): the model stopped normally (`STOP`) and wrote "No reliable 2026 data
found" in every section (kept in [flaky/](flaky/)). The callback flagged both runs and nothing was
invented. To find the cause, three direct Gemini calls with the exact same instruction, brief and
`google_search` tool (no ADK) all searched (3 queries each, all with the year). The request ADK
builds (captured with a stub `before_model_callback`, no model call) is the same apart from ADK's
one-line agent identity paragraph and the HTTP timeout/retry options. So there is no code
difference to fix; the most likely cause is the model or search service behaving inconsistently
between calls, and the samples are too small to say more.
A prompt cannot guarantee a search, so the fix is a retry: the Creative Director will re-ask the
Strategist once when it sees either warning (see `project-docs/plan.md`, M6 reliability).

| Instruction | ADK runs that searched | Ungrounded or failed |
|---|---|---|
| `b12e7c9` first version | 2 of 3 | `strategist_b` invented a source |
| `b9b0855` search every area | 2 of 5 | 1 "no data", 2 empty (`TOO_MANY_TOOL_CALLS`) |
| `4b45aee` capped searches (final) | 3 of 5 | 2 "no data" on brief A |
| Direct calls, final instruction (no ADK) | 3 of 3 | none |

**How to read the two sequence rows.** `sequence` (brief A, 11/12) was fed the brief-A research
from an earlier, ungrounded `strategist_a` run (kept as
[flaky/strategist_a_ungrounded_2.md](flaky/strategist_a_ungrounded_2.md)), so its captions had no
real research to build on and the reuse check correctly failed. The `strategist_a` row shows the
later run for the record, which searched. `sequence_b` (12/12) is the sequence proof: the
Copywriter was given the committed, grounded `strategist_b` research (3 searches, 9/9 checks) and
reused 42 of its distinctive terms. There is no brief-B Copywriter-alone run, so its reuse check
is the simpler "3 or more research terms" variant rather than the comparison used for brief A.
`sequence_b` and the final `strategist_a` run were made with a one-off driver that calls
`verify_m2.py`'s own `run_agent`, checks and evidence writer (not committed; no code changed).
Both are labelled `ac84894`; `git diff ac84894 HEAD -- workshop/` was empty when they ran.

**Checks added after the first runs** (each added because a real output exposed a gap):
the grounded-answer check (`d2284ee`), the non-empty answer check (`95790e2`) and the
research-failed check (`d155435`).

**403 attempts.** Two pre-fix attempts were rejected with `403 PERMISSION_DENIED` while the
billing account was a Free Trial. They are kept in `run_log.json` with `"counted": false`
and are not counted against the 15-run budget. The app code does not retry 403s.
