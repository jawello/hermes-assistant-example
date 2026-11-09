# Login-broker (`browser_login`)

An MCP stdio server (`login-broker/`, tool: `browser_login(url)`) that logs
the personal bot into a whitelisted site using credentials from Vaultwarden,
without ever exposing the password or TOTP secret to the LLM's context or to
any log/output.

## Architecture

- **Vault:** Shells out to the Bitwarden CLI (`bw`) pointed at the internal
  Vaultwarden instance (`https://vaultwarden:80`) to fetch a matching login
  item. On first use it authenticates: `bw login <account-email>` when the
  CLI is unauthenticated, otherwise `bw unlock`, always taking the master
  password via `--passwordenv` (never on argv). The `BW_SESSION` token lives
  only in the broker process's memory — never written to disk, never logged.
  Because Vaultwarden serves a self-signed cert, `bw` (Node) is pointed at it
  via `NODE_EXTRA_CA_CERTS=/ssl/vw.crt` (the cert is mounted read-only into
  the `hermes` container from the `vaultwarden_tls` volume). The unlock is
  **lazy** — the MCP server starts even when the vault is not yet
  bootstrapped/reachable, and `browser_login` returns
  `{"ok": false, "reason": "vault unavailable"}` until it is.
- **Origin matching:** Host-based matching (`login_broker/matching.py`)
  between the target URL and the vault item's stored URI. A leading `www.`
  is treated as equivalent to the apex (so `example.com` and
  `www.example.com` are the same site), but there is no other wildcard or
  subdomain leniency and ports must match — a vault entry for `example.com`
  will never be used to log in to `sub.example.com` or `example.com:8443`.
  Only the scheme is ignored. The `browser_login` tool also defaults a
  scheme-less argument (`example.com`) to `https://` before matching.
- **Browser automation:** Drives the existing headless-Chromium container
  over CDP (`http://172.28.0.10:9222` — the same endpoint Hermes' built-in
  `browser_*` tools use) via Playwright. It connects to the running browser
  rather than launching its own, fills the login form, and submits it. After
  navigating, it re-checks that the page actually landed on the same host the
  vault entry was selected for; if a redirect took it to a different origin,
  it aborts without typing the credential.
- **TOTP:** Where a vault item has a stored TOTP seed, `login_broker/totp.py`
  generates the current 6-digit code via `pyotp` at the moment of use. The
  seed and generated code never leave this module's call stack except to be
  typed into the page directly by Playwright.
- **Guardrail:** `login_broker/redact.py`'s `assert_no_secret_leak()` is
  run against every payload about to leave the process (MCP tool response,
  log line) and raises if any known secret string is found anywhere in the
  serialized output — see "No secrets, guaranteed" below.
- Registered in `hermes/config.yaml` under `mcp_servers.login-broker`, same
  pattern as the `ticktick` MCP server, and installed into the `hermes`
  image alongside it.

## Adding a new site to the vault

Login items live in the AI-browsing Vaultwarden instance, not the user's
personal vault. Follow the "Bootstrap" steps in [`docs/vaultwarden.md`](vaultwarden.md#bootstrap-one-time):

1. Open the web vault (`ssh -L 8222:172.29.0.10:80 deploy@203.0.113.10` then
   `https://localhost:8222`).
2. Add a login item: username, password, and a URI with match type **Host**
   (exact host only — the login-broker refuses anything else, including
   subdomains and mismatched ports).
3. If the site has 2FA, add the TOTP seed to the item as well.

Once the item exists, `browser_login("https://the-site.com/login")` will
find it automatically by exact hostname match — no code changes needed.

## Verifying end-to-end

From Telegram, ask the personal bot to log in to the test site added to the
vault (e.g. "log me into example.com"). The bot should call `browser_login`,
report success without printing any credential, and you can then confirm
via `browser_snapshot` that the resulting page shows an authenticated
session (e.g. an account/dashboard view rather than a login form).

As part of this check, grep the logs of every service in the stack
(`hermes`, `vaultwarden`, `chromium`) for the plaintext test password and
TOTP code — neither should appear anywhere.

## Rotating `BW_MASTER_PASSWORD`

The login-broker's Vaultwarden master password is stored as a sops secret,
not in any config file or environment file on disk in plaintext.

1. Change the master password for the `ai-browser@vault.local` account in
   the web vault (or admin panel) itself.
2. Update the secret in the repo's sops-encrypted secrets file:
   `sops set secrets.sops.yaml '["env"]["BW_MASTER_PASSWORD"]' '"<new-password>"'`
3. Restart the `hermes` service so the login-broker picks up the new value:
   `docker compose restart hermes`

## No secrets, guaranteed

No service in this stack ever logs a password or TOTP code/seed in plaintext:

- The Vaultwarden CLI wrapper passes the master password to `bw` via the
  environment, never as a CLI argument (which would land in
  `/proc/<pid>/cmdline` on the host), and any error raised on unlock failure
  is scrubbed of the password before it can bubble up.
- The Playwright login flow takes credentials as plain function arguments
  and uses them exactly once via `fill()`; it never logs or returns them.
- Every dict returned by the MCP `browser_login` tool is checked by
  `assert_no_secret_leak()` against the known secret values (password, TOTP
  seed, generated TOTP code) before it is allowed to leave the process —
  usernames in successful responses are masked (e.g. `the user` → `an****`).

This is enforced by the login-broker's own test suite (see
`login-broker/tests/test_redact.py`, `test_vault.py`, `test_browser.py`,
and `test_server.py`), all of which assert that secret material never
appears in a tool response, log line, or exception message. End-to-end
verification additionally greps every container's logs for the plaintext
test password to confirm it never appears.
