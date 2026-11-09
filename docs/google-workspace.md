# Google Workspace (Drive, Docs, Sheets, Calendar, Contacts, Gmail)

hermes reaches Google through its bundled `google-workspace` skill
(`$HERMES_HOME/skills/productivity/google-workspace/`), not through an MCP
server of ours. The skill runs `scripts/google_api.py` from the agent's
terminal; the Google client libraries come from hermes' `google` extra,
installed in `hermes/Dockerfile`.

Connected 2026-10-06. the user granted every scope the skill asks for, Gmail
included, so mail is reachable two ways: `gmail-mcp` (with its exclusions and
poller) and the skill.

## State

Both files are on the `hermes_state` volume, not in the repo or in sops:

- `$HERMES_HOME/google_client_secret.json` — the same Desktop OAuth client
  `gmail-mcp` uses (`GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` in
  `secrets.sops.yaml`), Google Cloud project `<your-project-number>`.
- `$HERMES_HOME/google_token.json` — the refresh token.

The project has the Gmail, Drive, Docs, Sheets, Calendar and People APIs
enabled. A `403 ... has not been used in project ... or it is disabled` means
an API is off there, not a bad token.

## Re-authorize (lost volume, revoked token)

Run inside the hermes container (`docker exec -i personal-assistant-hermes-1 …`):

1. Write the client file from sops without printing it: build
   `{"installed": {"client_id", "client_secret", "auth_uri", "token_uri"}}`
   from the two `GMAIL_*` values and pipe it into
   `$HERMES_HOME/google_client_secret.json` (mode 600).
2. `python $HERMES_HOME/skills/productivity/google-workspace/scripts/setup.py --auth-url`
   and open the URL in a browser.
3. The browser lands on `http://localhost:1/?...&code=...&scope=...` and
   fails to load (`ERR_UNSAFE_PORT`) — expected. Pass the whole URL to
   `setup.py --auth-code '<url>'` within a few minutes.
4. `setup.py --check-live` should print `LIVE_CHECK_OK`.

## Don't `--revoke`

The skill's own troubleshooting answers `403 Insufficient Permission` with
`setup.py --revoke`. That revokes the grant at Google for the OAuth client
`gmail-mcp` shares, which can sign `gmail-mcp` out as well. Delete
`google_token.json` and re-authorize instead.
