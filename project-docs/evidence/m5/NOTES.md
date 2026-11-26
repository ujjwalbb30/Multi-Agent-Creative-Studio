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
