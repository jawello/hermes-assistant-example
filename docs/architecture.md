# Architecture

One docker compose project. Every service gets the smallest set of networks
and secrets it needs; the agent, which reads untrusted text all day, holds
none of the strong credentials.

## Services

| Service | Role | Networks |
|---|---|---|
| `hermes` | the agent: Telegram gateway, scheduled jobs, skills, CLIs (`remember`, `suggestions`, `pass-window`, `zenmoney`) | default, aibrowse, memnet, telegram |
| `hindsight` | memory API: extracts facts from turns, recalls them by meaning and by words | memnet, default |
| `hindsight-db` | PostgreSQL with pgvector for the memory graph | memnet (internal) |
| `chromium` | shared headless Chromium, driven over CDP | aibrowse |
| `login-broker` | MCP tool `browser_login`: unlocks the vault with `bw`, fills the login form in chromium, returns only ok/not ok | vault, aibrowse, default |
| `vaultwarden` + `vault-cert-init` | credential vault (self-signed TLS, generated once) | vault (internal) |
| `gmail-mcp` | Gmail: search, read, drafts, archive/trash; send and trash ask the owner to confirm | default |
| `telegram-mcp` | the owner's Telegram user account (not the bot): reading, history search, a send gate with a confirm button and a chat allowlist | telegram |
| `alice-bridge` | webhook for a Yandex Alice skill; answers Alice within its time limit and hands the phrase to hermes; speaks hermes' short answer on the Station through a Quasar cloud scenario (`/speak`) | default, edge |
| `shared-janitor` | prunes the `/shared` exchange volume (mail attachments, downloaded media, outbox) | default |

TickTick is the vendor's hosted MCP server, reached over HTTPS with a static
token.

## Trust boundaries

- **Mail and chat text are data, never instructions.** Every skill and cron
  prompt says so, and scheduled jobs are pinned to the toolsets their pass
  needs (`set_job_toolsets` in `hermes/entrypoint.sh`): a mail pass has the
  terminal for its CLIs, files, skills and the gmail server — no browser, no
  login broker, no code execution.
- **Writes that leave the system are confirmed by a human.** `gmail-mcp` and
  the telegram send gate use MCP elicitation: the owner gets a button with the
  recipient and the exact text, and only an accepted button sends.
  `hermes/patches/mcp_session_context.py` makes those elicitations reach
  Telegram.
- **The agent never sees a password.** It asks `browser_login` to log in to a
  host; the broker finds the vault entry by exact hostname, types the
  credentials into the shared browser itself and redacts them from anything it
  returns.
- **Skills change only with approval.** The agent edits its own skills, but
  with `write_approval` every write waits for `/skills approve`.

## Memory

Hindsight replaces hermes' built-in `MEMORY.md`/`USER.md` files. Chat turns
are captured automatically; scheduled passes store facts explicitly with
`remember`, keyed by a document id per source message so a re-read window
replaces facts instead of duplicating them. Patches in `hermes/patches/` and
`hindsight/patches/` keep Russian text as UTF-8, query by the user's own words,
skip recall for cron passes and run the reranker on ONNX to fit a small VPS.

## Scheduled passes

`ensure_cron` in `hermes/entrypoint.sh` recreates the jobs on every start, so
the repo wins over the volume. Each pass reads from a checkpoint
(`hermes/bin/pass-window`): it starts where the last finished pass started,
and moves the checkpoint only when every read succeeded, so a failed run is
caught up by the next one.

## Backups

`ops/backup.sh` archives the stateful volumes nightly and mirrors them to
Google Drive through an rclone crypt remote; restore steps are in
`docs/backup-restore.md`.
