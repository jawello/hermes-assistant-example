---
name: suggestions
description: Morning task suggestions — how the daily job proposes 0–3 tasks, and how to handle the owner's reply to them ("да 1, 2", "нет", "сделано 2", "1 — на пятницу").
version: 4
---

# Task suggestions

Every morning a scheduled run looks at what memory knows and proposes up to
three tasks. The owner answers in chat. The two sessions share nothing but the
ledger, which only the `suggestions` command touches — never read or edit
`ledger.jsonl` yourself.

## A. The morning run

1. Run `suggestions context`. It prints the ledger (what was already proposed,
   with statuses) and what memory holds: dated items, and facts from the last
   seven days. If it exits non-zero, say what broke, in one line — do not
   answer `[SILENT]`, a broken input is not a quiet morning.
2. Settle what the owner already handled. For each `pending` entry in the
   ledger, `search_task` on its key words. A task on the same subject, open or
   completed, created on or after the suggestion's date, means it was taken up
   without an answer: `suggestions set-status <id> accepted`. Not sure it is
   the same subject → leave it pending.
3. Check each candidate against TickTick: `search_task` on its key words,
   `list_undone_tasks_by_time_query` with `next7day` for what is already due
   soon, and `list_completed_tasks_by_date` for the last 30 days — a task
   closed last week is as much "already done" as an open one.
   (`list_completed_tasks_by_date` returns a huge concatenated-object payload
   that the transport persists to a file — see ticktick rule **R10**; the
   ticktick skill's `scripts/parse_completed_tasks.py` parses it in one call.
   Parse it in `terminal`: `execute_code` is refused in cron runs.)
   `search_task` is keyword matching: compare titles yourself. If any of these
   calls errors, send no suggestions this run and say what broke, in one line —
   do not propose anything you could not check, and do not answer `[SILENT]`.
4. Choose **at most three** suggestions. A good one is concrete, has a reason
   the owner would recognise, and is not already a task. Drop a candidate when:
   - it is already in TickTick, open or completed in the last 30 days — same
     subject counts, even if the wording or the next step differs (a "ask the
     plumber when he can come" next to an existing plumber task is a
     duplicate);
   - the ledger has it (any status) — same subject and same source, even if
     worded differently; a rejected or done idea is never proposed again;
   - it needs no action, or the deadline has passed.

   A Telegram fact that someone is waiting for an answer is a fair candidate
   ("Ответить Косте про столик") — unless the same memory window also shows
   it was already answered.
5. Record each one: `suggestions add --title "..." --why "..." --source "..." [--due YYYY-MM-DD]`.
   Title is the action, in Russian, as it would read in TickTick. `why` is one
   short clause. `source` names where it came from and when ("письмо от
   провайдера, 18.09", "Telegram, чат с Костей, 04.10"). `due` only when the
   fact carries a date. If `add` exits non-zero for one, drop that
   suggestion — do not send it and do not retry.
   If none of them could be recorded, report the failure instead of sending
   suggestions or answering `[SILENT]`.
6. Run `suggestions message` and send its output **verbatim** as your whole
   answer — no preface, no edits. It numbers the suggestions the way part B
   reads them and builds the reply hint from those numbers.
7. Nothing clears the bar → answer exactly `[SILENT]`, with no spaces inside
   the brackets, and add nothing.

**Never write anything else.** Do not create, change, complete or delete
tasks, send mail, or save memories in this run. `suggestions add` and the
`suggestions set-status … accepted` of step 2 are the only writes it makes.

## B. The owner's reply in chat

Applies only when a message is plainly an answer to a suggestions list — it
comes right after one, or it names numbers, "все", "нет" or "сделано" in that
sense ("да 1, 3", "все", "нет", "сделано 2", "2 — на пятницу", "первое да,
остальное нет"). A message that just happens to contain a number or "нет" but
is not answering the suggestions list is not this case; handle it normally.

1. Run `suggestions list --pending`. No pending entries → the message is about
   something else; handle it normally.
2. Numbers always refer to the **newest** batch: the pending entries with the
   newest date; the number is the last part of the id (`s-2026-09-21-3` is 3).
   An older pending batch is not addressed by this reply — leave it alone and
   let it expire on its own. A number that is not in the newest batch → ask;
   never guess or skip it silently. When the newest batch has a single entry,
   "да", "сделано" or "на пятницу" without a number mean that entry.
3. For each accepted one, create a TickTick task in **Inbox** — never
   `self_tasks`:
   - title: the suggestion's title;
   - content: `why` and `source`, one line each;
   - dueDate: the date given ("на пятницу") or else the suggestion's `due`;
     none if neither. A date has no time, so send it as the ticktick skill's
     all-day shape (`isAllDay: true`, `startDate` = `dueDate` = local midnight);
   - tags from the existing tag list, only if one clearly fits.
   Then `suggestions set-status <id> accepted`. Mark it accepted **only after**
   the task was created.
4. "сделано N" means it was already done and no task is wanted: create
   nothing, `suggestions set-status <id> done`.
5. Every other entry of that batch that was declined or not picked:
   `suggestions set-status <id> rejected`. "нет" rejects the whole batch.
6. Reply with one line per created task (title and date) and one line
   "отмечено как сделанное: <title>" per `done`, and nothing else.

If creating a task fails, leave that entry pending and say so.
