# Running it

This runs the whole client with your own accounts. It is not packaged for
others: expect to read code when something does not fit your setup.

## What you need

- A Linux host with docker compose (the original runs on a small VPS).
- A Telegram bot (BotFather) and your numeric Telegram user id.
- An LLM provider hermes supports; `hermes/config.yaml` uses Nous Portal,
  logged in once with `hermes login` inside the container.
- For each integration you want, its account: Gmail (a Google Cloud OAuth
  client), your Telegram user account (API id/hash and a session string),
  TickTick (an API token), Zen-Money (a token), a Groq key for voice notes.

## Steps

1. `cp .env.example .env` and fill in what you use. Empty optional values
   switch features off.
2. Edit `hermes/config.yaml`: timezone, models, MCP servers you do not use.
   Edit the schedule and prompts in `ensure_cron` (`hermes/entrypoint.sh`).
3. Edit `hermes/SOUL.md` and the skills under `hermes/skills/` to your own
   conventions; personal values belong in memory, not in skills.
4. `docker compose build`, then `docker compose up -d`.
5. Vault: follow `docs/vaultwarden.md` to create the account login-broker
   uses, then add site logins as described in `hermes/SOUL.md`.
6. Gmail and Telegram user account: mint their tokens on your own machine
   (see the comments in `.env.example`), never on the server.

`bash scripts/check.sh` runs every test with dummy values.

## Which services need your accounts

Observed on a local run with dummy values in `.env` (2026-11-06): every
service builds and every healthcheck turns healthy, but the healthchecks do
not test accounts. What actually works without them:

| Service | With dummy values |
|---|---|
| `hindsight-db`, `chromium`, `vaultwarden`, `vault-cert-init`, `shared-janitor` | fully working; no external account involved |
| `hindsight` | the API is up; extracting facts needs `HINDSIGHT_LLM_API_KEY` |
| `alice-bridge` | up, rejects every request until the Alice skill values are set |
| `gmail-mcp` | up; every Gmail call fails until the OAuth client and refresh token are real |
| `login-broker` | up; `browser_login` needs the vault account and site entries |
| `telegram-mcp` | the server exits on the invalid session and the wrapper parks it, retrying every 10 minutes; needs a real session string |
| `hermes` | the gateway starts, but Telegram needs a real bot token and the model needs a provider login; MCP servers that are down are parked and retried |

TickTick and Zen-Money are reached only from hermes, with their tokens.
