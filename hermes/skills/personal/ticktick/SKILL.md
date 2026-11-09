---
name: ticktick
description: Manage TickTick tasks — search, filter, move, reschedule. Covers the official MCP server's call shapes and the owner's conventions.
version: 14
---

# TickTick Task Management

Backend: TickTick's official hosted MCP server, authenticated with one static
API token. Behaviour rules below (§5) were measured against the live server
(September 2026), not assumed.

## 1. Conventions

These are one owner's conventions; edit them to match yours.

- **Closed tag list.** Use only tags that already exist (`list_tags`). Never
  invent a tag; if unsure, check first.
- **Shopping tasks → the household tag.** Always add the owner's household tag
  (e.g. `дом`) to tasks for purchases, including tasks in the Shopping project.
  Retain any other applicable tags.
- **Default durations.** When the owner gives no time span for a routine
  appointment, set one yourself (via `startDate` + `dueDate` covering it) from
  a short table kept here, e.g. — **звонок → 30 мин**, **встреча → 1 час**.
- **Inbox is the default project.** New tasks go to Inbox (`inbox`) unless the
  owner names another project.
- **A long-term recurring project is reserved** for genuinely recurring tasks.
  Never file a one-off task there.
- **The assistant's own tasks → `self_tasks`**, a separate project for work on
  the assistant itself, with the tag `ai` only.
- **A task with a date but no time is an all-day task.** In TickTick "no time" means `isAllDay: true`. So when the owner names a day and no time, send `isAllDay: true` with `startDate` and `dueDate` both at local midnight (with the current UTC offset), e.g. `{"isAllDay": true, "startDate": "2026-11-05T00:00:00+0200", "dueDate": "2026-11-05T00:00:00+0200", "timeZone": "<the owner's zone>"}`. TickTick then shows just the date. Do not turn a day or a broad window such as «утром» into a guessed clock time; keep the window in the task text. **A bare date only looks safe here.** TickTick itself stores a bare `dueDate: "YYYY-MM-DD"` as `isAllDay: false` at 00:00. The `ticktick_untimed` plugin rewrites such a call into the all-day shape before it leaves hermes, so a bare-date call that reads back `isAllDay: true` shows the plugin working, not the server changing. Do not rewrite this rule from such a read-back. A real midnight appointment needs an explicit `isAllDay: false`.
- **Anchor relative dates to their speaker and source.** When creating a task from a quoted digest or older message, resolve «завтра» against the original message's local date, not the date the owner asks you to create the task; otherwise a delayed request silently shifts the deadline. If the source date cannot be recovered, ask rather than inventing a date. When the owner corrects a misheard date in a follow-up, use the correction and preserve any separately stated clock time; check whether the interrupted earlier attempt actually created a task before making another.
- **Rescheduling preserves the time.** When moving an existing task to another
  date, keep its original time-of-day; change only the date.
  `2026-08-02 19:15` → `2026-08-03 19:15`, never `2026-08-03`. Never reset a
  time to 00:00 unless explicitly asked. How to do this on this server: rule
  **R4** in §5.
- **«вечером» = 19:00 local** when no time is given. For errands at a venue that closes earlier, schedule a feasible time before closing and say so; do not set a task for after the venue shuts. Check the current local time and published opening hours first.
- **Read an untimed task back.** After creating or moving one, `get_task_by_id` must return `isAllDay: true`. `isAllDay: false` with `00:00` means the flag was lost — fix it with `update_task` (both dates, `projectId`, `isAllDay: true`) before saying the task is done.

### Task content conventions

- **Tasks to reconstruct a forgotten discussion:** Search existing tasks and available history for the original context before writing the new task. Record only recovered points as facts; put uncertain terms in the task as questions to clarify rather than silently converting them into criteria or commitments. Make the task's outcome explicit: save the reconstructed list, decisions, and next steps in the task after the conversation, so the context persists. If the prior list remains unfound, say so briefly instead of inventing it.
- **«Задачи на сегодня»** through the API covers TickTick tasks only: filter all projects plus the Inbox for the day. Calendar-synced events and the app's sort order are not in the API, so do not present the list as the full day.
- **Location tasks**: Every task tied to a place gets a Google Maps link. Retrieve the known address and branch from memory or the relevant domain skill before building a Maps link. Open the specific Google Maps place and compare its address, branch/building and phone with the known details (and the venue's official branch page when available); same-name search results may point to another location. Put the direct place/pin URL (`maps.app.goo.gl/…` or `google.com/maps/place/…`) alongside the address in the task, not a `maps/search` URL when the place can be identified. Read the saved task back and check the link survived intact. If only a search URL is available, label it as a search, not a verified pin; for a private destination without a known address, never fabricate a pin — record that the address is unknown and ask for it if navigation matters.
- **“After X” with a duration or ordered errand chain**: look up X's actual end time and all active errands already scheduled after X on the requested local date before editing the chain. Check locations, real travel times and closing times before assigning clock times; do not pick an arbitrary branch or invent a tight arrival window. When a new order conflicts with an existing follow-on errand, adjust its sequence only if the new order clearly includes it; otherwise preserve the task and explicitly flag the unresolved position. If a stop's location is unknown, keep the sequence in task text without a guessed time or map pin. A tentative time must distinguish departure from arrival. Resolve corrections like “today, not tomorrow” against the current local date, search for an already-created matching task before adding one, then read back each changed task.
- **Parcel pickup tasks**: first check the day's existing appointments and pickup tasks; when a pickup is described by its contents, reconcile it with today's active parcel tasks and the original order evidence before creating another task. A colloquial item name may be an existing parcel, not a new stop; if contents cannot be verified, ask rather than assert a match. Search for an active task for the *same order/tracking number* before creating or rescheduling — an older completed pickup at the same venue is not the same parcel. If a mistaken duplicate was created, delete only that duplicate and correct cross-references in the errand chain. If a screenshot follows a pickup request, extract its order, tracking, locker location and collection deadline, then complete the pending task without asking to repeat the request. Verify ambiguous tracking glyphs such as `0`/`O` against the image; preserve identifiers exactly. A host business's opening hours do not establish a self-service locker's access hours; when unverified, keep the task date-only with the uncertainty in the content. Put carrier, exact tracking number, pickup point, collection deadline and ID requirement in the content when known. Always put the delivery/pickup PIN in the content, read precisely from the original message or image; with two PINs, list both. This covers parcel PINs only: a bank card PIN or a login code never goes into a task or memory.
- **A photo or file for a task** («фото приложи к задаче»): TickTick's API
  takes no attachments (neither this server nor the Open API), so the file
  goes in as a Drive link. Get the file: from someone's Telegram message,
  `download_media(chat_id, message_id)` (the telegram skill) returns its path
  under `/shared/telegram/`; one the owner sent you is already on disk. Upload it:
  `python ${HERMES_HOME}/skills/productivity/google-workspace/scripts/google_api.py drive upload <path> --name "<what it shows>.jpg"`
  and put its `webViewLink` in the task content («📷 фото: <link>»). Never
  say a file is attached when only a link is; say "фото — ссылкой в задаче".
  Upload or download failed → create the task anyway, with where the photo is
  (chat, date, time), and say what failed.
- **Errands**: title pattern «Отвезти [кому] [что] для [кого]», with the
  recipient's location link in the content.
- **Shopping review («что я забыл купить?»)**: read the open Shopping project tasks, then search Hindsight for purchase intentions not in TickTick; distinguish unbought plans, orders awaiting delivery, and confirmed purchases. A completed TickTick task only confirms the task was closed, not that the purchase occurred. Say when the last evidence is old and ask whether a plan still matters. Do not expose parcel collection PINs in a shopping recap.
- **Shopping**: shop name, address and phone in the content, plus a
  `- [ ] item` checklist of what to buy. For follow-up additions, read the
  existing task first; update the matching checklist item in place rather than
  adding a near-duplicate. When sending the full `items` array to
  `update_task`, retain existing item IDs and statuses so checked-off goods
  stay checked. Read the task back.
- **Tasks from a video recipe:** Open the linked video or a reliable extracted transcript before writing the task; put the verified ingredients, amounts, method, timing and source URL in its content. Distinguish optional toppings from required ingredients. Preserve a broad window such as «утром» in the text without assigning an arbitrary hour; read back the saved task.

## 2. Call shapes — arguments are wrapped

Task, filter and move payloads are **nested objects**, not flat keyword
arguments:

```json
create_task  {"task": {"title": "Позвонить в сервис", "projectId": "inbox", "dueDate": "2026-09-12T16:00:00+0000"}}
update_task  {"task_id": "<task id>", "task": {"dueDate": "2026-09-13T16:00:00+0000"}}
filter_tasks {"filter": {"startDate": "2026-09-01T00:00:00+0000", "endDate": "2026-09-30T00:00:00+0000", "priority": [5], "status": 0}}
move_task    {"moves": [{"fromProjectId": "inbox", "toProjectId": "<project id>", "taskId": "<task id>"}]}
```

- `filter.priority` is an **array**, even for a single priority: `[5]`.
- `move_task` takes an **array** of moves, one object per task.
- A flat call (`{"title": …}` instead of `{"task": {"title": …}}`) fails with a
  pydantic `Field required` error naming the wrapper you left out. Add the
  wrapper it names and retry **once**. Never loop on the same shape.

## 3. Tool map — what to reach for

| Need | Tool |
|---|---|
| What's overdue / due soon | `list_undone_tasks_by_time_query` with `query_command`: `today`, `last24hour`, `last7day`, `tomorrow`, `next24hour`, `next7day` |
| A precise date window, or filtering by project/tag/priority/status | `filter_tasks` with an explicit `startDate`/`endDate` range |
| Find a task by text | `mcp_ticktick_search` (works; R9) — then confirm the id (R7) |
| One known task | `get_task_by_id` |
| Create / change many tasks at once | `batch_add_tasks` / `batch_update_tasks` |
| Move tasks between projects | `move_task` |
| What was completed | `list_completed_tasks_by_date` |
| Complete several tasks in one project | `complete_tasks_in_project` (max 20 — see R5) |

Prefer a targeted query over listing everything.

Calendar meetings that TickTick shows from a subscribed calendar are not in
its API: no tool here returns them. Say so when asked about the whole day.

## 4. Other useful tools

- `list_tags` — check before using a tag you are not certain about.
- `add_comment` — adds a comment to a task; usually a better fit than
  rewriting a task's content to append a note.
- `list_project_groups` — project folders.
- `list_countdowns` — countdown items.

## 5. Behaviour rules (measured)

**R1 — `create_task` sets dates in one call.** Put `dueDate` and `startDate` (plus `isAllDay: true` for a day with no time) directly in the `task` payload; the same goes for every item of `batch_add_tasks`. No follow-up `update_task` is needed to make a date stick.

**R2 — Never read the moved task's fields off a `move_task` response.** It
returns `structuredContent.result` as an array with `id`, `isAllDay`, `etag`
and `modifiedTime` populated and everything else — `projectId`, `title`,
`startDate`, `dueDate`, `status`, `tags` — null. To confirm where a task
landed, call `get_task_by_id` afterwards.

**R3 — Inbox can be addressed two ways.** Both the literal string `"inbox"`
(as `list_projects` advertises it) and the account's real inbox id (as it
appears in every task object, `inbox<digits>`) work as
`toProjectId`/`fromProjectId`; the server resolves the literal to the real id.

**R4 — Reschedule with both dates and the project ID.** Read the task first,
then pass `projectId`, `startDate` and `dueDate` inside `task` when moving a
scheduled task. Preserve any original time-of-day in both dates. Updating
only `dueDate` can leave `startDate` unchanged, resulting in start>due; omitting
`projectId` can make the write fail. Read the task back to confirm both dates.

**R5 — Only `complete_tasks_in_project` has a documented cap: 20 task ids per
request.** `batch_update_tasks` and `batch_add_tasks` state no limit — do not
invent one or split batches for a rule that isn't there. If a very large batch
ever errors, that's a new finding to report, not a known limit.

**R6 — Casing is per-tool, not global.** Flat scalar arguments are snake_case
(`task_id`, `project_id`, `task_ids`, `query_command`); fields inside nested
request objects are camelCase (`task.projectId`, `task.dueDate`,
`filter.projectIds`, `moves[].toProjectId`). Response bodies are camelCase —
**except `list_undone_tasks_by_time_query`, which returns snake_case**
(`project_id`, `start_date`, `due_date`, `is_all_day`). Parse that one
specially. The response casing comes from live-captured bodies, while the
argument casing was read off each tool's published schema — so on an
unfamiliar tool, check its schema before assuming an argument name's shape.
Before parsing a tool you haven't used here, print one real response instead
of guessing a key name.

**R7 — `search_task` is keyword matching, not an exact-title lookup.** A query
sharing a common word with unrelated tasks will surface them; a hit is not
proof you found the right task, and an empty result is not proof a task does
not exist. Always compare the returned `id` (or exact `title`) against what
you were looking for. Observed: a truncated word returned nothing while the
full word found the task — matching is full-word/lemma based, not substring.
When an empty result matters, retry with the full word and with synonyms
before concluding a task is absent.

**R8 — `delete_task` is lenient about `task_id`, strict about `project_id`.**
Deleting a `task_id` that doesn't exist returns `isError: false` and echoes
`{"deleted": "<id>"}`, so a successful delete does not prove the task existed
or was removed — confirm with `get_task_by_id` when it matters. A wrong
`project_id` with a real `task_id` is a hard failure (`isError: true`); treat
that as a real error and fix the project id rather than retrying.

**R9 — two search tools; only `mcp_ticktick_search` works.** The hosted server exposes *both* `mcp_ticktick_search` and `mcp_ticktick_search_task`; the latter fails on every call with `TickTick API error [unknown_exception]`. Always use `mcp_ticktick_search`. Its matching is very loose — a short query returns dozens of unrelated tasks, so eyeball returned titles/ids against what you actually want (R7). All of these tools are prefixed `mcp_ticktick_*`. In chat they sit behind `tool_search` and are reached via `tool_call`, never as bare names; in the `suggestions` job they are listed directly, so call them as ordinary (parallel) tool calls there.

**R10 — `list_completed_tasks_by_date` returns a huge concatenated-object payload, not an array.** A 30-day window comes back as ~300+ pretty-printed JSON task objects simply concatenated one after another (a `json.loads` on the whole thing fails with `Extra data:`). It is easily 400KB, so the transport persists it to a file and returns only a short preview. Read the persisted file with the ready-made script (`python3 /data/hermes/skills/personal/ticktick/scripts/parse_completed_tasks.py <file>`), which walks it with `json.JSONDecoder().raw_decode` and prints sorted `completedTime | projectId | title` lines. In cron runs `execute_code`, `python3 - <<'EOF'` heredocs and `python3 -c` are all blocked as unapproved script execution; running the script file is allowed.
Call shape (measured): `{"search": {"startDate": "2026-09-04T00:00:00+0300", "endDate": "2026-10-04T23:59:59+0300"}}` — a nested `search` wrapper with camelCase keys and ISO-8601 timestamps carrying the offset.

## 6. Failure handling

- **MCP transport teardown / unreachable:** Treat `transport is down; reconnect requested` and a subsequent auto-retry cooldown as connection failures, not as rejected task data or revoked credentials. Wait for the stated cooldown, then probe with a read-only call such as `list_projects`; on recovery, resume the requested operation. Do not hammer the same tool while the transport is reconnecting. If a write may already have been dispatched before the teardown, search/read the exact target before retrying to avoid duplicates.
- **`401`** — the static API token was revoked or rotated. There is nothing
  the agent can re-authenticate. Stop, report it, and ask the owner to issue
  a new token. Do not retry in a loop.
- **`isError: true` with a pydantic message** (`Field required`, type errors) —
  the arguments were shaped wrong, not the request rejected. Fix the shape per
  §2 and retry once.
- **Writes failing while reads succeed** — not observed on this server. If it
  ever happens, retry once or twice, then report the blocker and offer to add
  the task manually. It is not an auth problem — that surfaces as `401`.
