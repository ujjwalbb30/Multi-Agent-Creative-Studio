## Notes on this evidence

**Incident: the Notion token appeared in local tool output (fixed before any agent run).**

- *What leaked:* the Notion integration token. While checking the MCP response shape, one
  read-only `API-retrieve-a-database` call was made with a deliberately invalid database ID.
  `@notionhq/notion-mcp-server` 1.9.1 handled the 404 by writing the whole axios error object to
  its stderr, including the request's `Authorization` header with the token. That stderr was
  inherited by our process and appeared in the local session output. Nothing was written to the
  repo, evidence or a PR, and nothing was pushed.
- *Why:* the MCP server's own error logging, not our code. Our code masked everything it printed,
  but the child process's stderr bypassed it. On Cloud Run the same stderr would go to Cloud
  Logging, so every failed Notion call would have written the token into the logs.
- *Response:* the token was rotated immediately; the old one is revoked.
- *Fix:* the Project Manager starts the server as `sh -c 'exec notion-mcp-server 2>/dev/null'`,
  so its stderr never reaches our process or the logs. Our own error lines come from
  `handle_notion_error` / `handle_notion_exception`, with IDs masked to the last 4 characters and
  token-shaped text redacted.
- *Regression test:* `workshop/starter/scripts/test_m5_pm_offline.py` starts the real server with
  a FAKE token and makes a failing call through a dead local proxy (nothing reaches Notion). It
  fails if the fake token, a bearer header or the full database ID appears anywhere in the
  captured stdout or stderr. A control run without the wrapper must leak, which proves the test
  can see a leak. No real token is used in any test.
- *Guards:* `workshop/starter/scripts/check_secrets.py` scans every tracked file (and any PR text
  passed to it) for token-shaped text and for the actual configured token, database IDs, project
  ID, project number and bucket name, printing only file:line and what kind of value matched. It
  runs as a git pre-push hook (`workshop/starter/scripts/git-hooks/pre-push`) and in
  `verify_m5.py`. CLAUDE.md now forbids reading `.env` values and requires masking IDs.

**Also found while fixing it:** a connection failure makes the MCP call *raise* (`McpError`)
instead of returning an error response, which would have crashed the run. The agent now has an
`on_tool_error_callback` that turns it into a masked recovery hint, so the text timeline is
always produced.

**Owners go into task titles.** The Notion template's owner fields (`Owner` on Projects,
`Assignee` on Tasks) are *people* properties, which an integration token cannot set. The
instruction tells the Project Manager to skip them and put the owner's role in the task title
instead, e.g. "[Copywriter] Publish The Productivity Pivot". The text Task List keeps a proper
Owner column.

**Finding: quoted property keys, repaired on every write.** In the first Notion run (kept in
[pre-fix/pm_notion.md](pre-fix/pm_notion.md)) nothing was created: the model wrote the title
property as `"Project name"` with literal quote marks inside the key, and once used
`parent.page_id` with a database ID. Checking the cause: the provided Notion instruction contains
**no** quoted or escaped property names (its only quoted words are type names such as "people"
and "relation", and JSON examples whose backslashes are Python escaping, so the model sees plain
quotes). The more likely source is the MCP tool definition: `API-post-page`'s `properties`
parameter only declares a fixed `title` key, so the model has no schema-sanctioned way to write a
key like `Project name` and escapes it. Only keys containing a space were quoted. Commit
`dcfa632` added a narrow `before_tool_callback` repair (a configured database ID passed as
`page_id` becomes `database_id`; a key that matches a recorded schema name after stripping quotes,
backslashes and whitespace is renamed; nothing else is guessed and no values change), a plain-key
example in the instruction, and a 400 hint that lists the valid property names. **The repair fired
on every Notion property write in both normal runs: 8 of 8 page writes in `pm_notion` and 8 of 8
in `pm_notion_2` (16 of 16, all quoted keys).** The parent repair did not fire in these runs. The
instruction example alone did not change the model's behaviour; the repair is what makes the sync
work.

**Finding: the model reached the real Tasks database through the relation; we block that.** In
the first forced-error run (kept in
[pre-fix/pm_notion_forced_error_via_relation.md](pre-fix/pm_notion_forced_error_via_relation.md)),
the Tasks database ID was overridden with one that does not exist. The 404 was rewritten into the
hint as designed, but the model then read the real Tasks database ID from the Projects database's
`Tasks` relation and wrote all 8 tasks there, bypassing the configuration. Commit `1442ec3` allows
page creation (`API-post-page`) only when `parent.database_id` is one of the two configured IDs;
anything else is blocked with a masked "not a configured database" hint that counts toward the
error cap. In the rerun the project was created, no tasks reached the real Tasks database, and the
Notion Status reported the task sync failure.

**Finding: the error cap is advice, not enforcement.** After three errors the hint tells the model
to stop using Notion, but in the forced-error rerun it kept trying: 8 page-creation attempts were
blocked in total. Every one was blocked, so nothing was written, but the extra calls cost model
turns. Hard-blocking all Notion calls once the cap is reached is noted for M6.
