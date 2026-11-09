# hermes-assistant-example

A personal assistant built on [hermes-agent](https://github.com/NousResearch/hermes-agent),
shown as an example of putting an AI agent to work on everyday life: mail,
Telegram, tasks, a budget, logins to websites, and a long-term memory. It runs
as one docker compose stack on a small VPS and talks to its owner in Telegram.

This is a snapshot of a real, private setup, with personal data taken out. It
comes **with no support** and no promise that every integration works for you.
Read it for ideas; if you want to run it, see `docs/running.md`.

## What it does

- **Reads mail and Telegram on a schedule** and remembers what will still
  matter in a month: who is waiting for an answer, what was agreed, dates,
  bills. It stays silent on a quiet day and reports only what needs attention.
- **Proposes up to three tasks every morning** from what it learned, checked
  against TickTick so it never suggests what is already planned or done.
- **Manages TickTick tasks** in chat, with measured rules for the hosted MCP
  server's quirks.
- **Splits receipts in a budget app** (Zen-Money): a photo of a receipt becomes
  categorized transactions, always confirmed first.
- **Logs in to websites without seeing passwords**: a separate login broker
  takes credentials from a Vaultwarden vault and fills them into a shared
  headless Chromium.
- **Sends messages and mail only on a button press**: every outgoing Telegram
  message and every sent email is confirmed by the owner.
- **Takes voice commands** from a Yandex Alice skill.
- **Remembers** through [Hindsight](https://github.com/vectorize-io/hindsight),
  a knowledge-graph memory, instead of a growing notes file.

## How it fits together

```
                     Telegram (owner)
                           │
                   ┌───────▼────────┐        TickTick hosted MCP
   alice-bridge ──►│     hermes     │──────► (tasks)
   (voice, edge)   │ agent, cron,   │
                   │ skills, CLIs   │──memnet──► hindsight ──► hindsight-db
                   └─┬──────┬─────┬─┘              (memory)     (pgvector)
            aibrowse │      │     │ telegram
                     │      │     └──────────► telegram-mcp (user account,
          ┌──────────▼┐     │                  send gate, history search)
          │ chromium  │◄────┤ default
          │ (CDP)     │     └──────────► gmail-mcp (read, drafts, confirmed send)
          └─────▲─────┘
                │ aibrowse
          login-broker ──vault──► vaultwarden (credentials)
```

Each integration runs in its own container with only the networks it needs:
the agent never holds the Gmail token, the Telegram session or the vault
password. Details: `docs/architecture.md`.

## What is deliberately left out

- Personal data of every kind: names, accounts, places, schedules, and the
  skills that are about health, money, documents or hobbies.
- Integrations that only make sense for the original owner (a work calendar,
  a media library, an e-book reader).
- The design notes and plans written along the way; they are full of live
  details. `docs/how-it-was-built.md` describes the process instead.
- Deployment: no images, no deploy workflow, no secrets. CI runs the tests
  with dummy values only.

## Layout

| Path | What |
|---|---|
| `docker-compose.yml` | the whole stack |
| `hermes/` | agent image, config, persona (`SOUL.md`), skills, patches, CLIs |
| `hindsight/` | memory service image and patches |
| `gmail-mcp/`, `telegram-mcp/`, `login-broker/`, `alice-bridge/` | MCP servers and bridges |
| `zenmoney-cli/` | budget CLI used by the zenmoney skill |
| `ops/` | backup script and shell tests |
| `docs/` | architecture, running it, how it was built, service notes |
| `scripts/check.sh` | every test and check, as CI runs them |

Licensed MIT; third-party notes in `THIRD_PARTY.md`.
