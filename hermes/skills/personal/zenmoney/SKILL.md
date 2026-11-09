---
name: zenmoney
description: Match a receipt (photo or text) to a bank-synced Zen-Money transaction and split it by budget category, or create a new transaction from a receipt. Use whenever a message contains a receipt, or mentions Zen-Money / Дзен-Мани / "разнеси чек" / "добавь в бюджет".
version: 1.1.2
platforms: [linux]
metadata:
  hermes:
    tags: [budget, receipts, zenmoney]
    category: personal
---
# Zen-Money: receipts → categorized transactions

The owner keeps a budget in Zen-Money (Дзен-Мани). Bank card payments sync
into it automatically as single uncategorized transactions. Your job: take a
receipt, find that synced transaction, and split it into budget categories —
or create a new transaction when nothing synced (cash payments).

The `zenmoney` CLI does all API work. Every command auto-syncs first and
prints one JSON object. On error it prints `{"ok": false, "error": ...}`.

## Workflow

1. **Read the receipt.** Photo → read line items with vision; text → parse as
   is. Note the store name, date, and total.
   - **Built-in vision first.** When the message already carries a system
     vision description of an attached image, use it — it is the same pixel
     data and saves a round-trip. Call `vision_analyze` only when that
     description is missing or too vague (missing line items, illegible
     prices).
2. **Get the real categories first:** run `zenmoney tags` — its output is the
   ONLY valid set of categories. Split parts must use these names verbatim.
   Never invent a category and never use a name from memory or old notes that
   is not in the list.
3. **Recall learned mapping rules:** `hindsight_recall` context
   "budget дзен-мани" for how the owner maps goods to categories. Apply a rule
   only when its target category exists in the `zenmoney tags` output. If a
   rule points at a category that doesn't exist, or an item has no obvious
   category, put your best existing-category guess in the plan and mark it
   with "?" so the owner corrects it before confirming. When corrected,
   `hindsight_retain` the mapping (item/store → real category name).
4. **Group items into category sums.** The sums MUST add up exactly to the
   receipt total. Show your grouping math if anything is ambiguous.
5. **Find the synced transaction:**
   `zenmoney find --date 2026-07-19 --amount 38.60 --payee <store>`
   Results are ranked; `"match": "exact"` + `"payee_hit": true` is a confident
   hit. `"near"` means the amount differs — treat with suspicion.
6. **Present the plan and WAIT.** Show the matched transaction
   (`zenmoney show <txid>` for details) and the proposed parts using the real
   category names, e.g.: "Магазин 38.60€ 19.07 → Продукты 30.10 +
   Хозтовары 8.50. Разбиваю?" Do NOT proceed without an explicit OK.
7. **On OK — split:**
   `zenmoney split <txid> "Продукты=30.10" "Хозтовары=8.50"`
   The CLI shrinks the original to the first part and creates the rest as new
   transactions (same account/date/payee), all in one API call. It rejects
   any category name that doesn't resolve against the real tag list.
8. **No synced transaction found** (e.g. cash): propose
   `zenmoney create --date ... --amount ... --payee ... --tag "Продукты"`
   instead — also confirm-first.

### Splitting a bill among people

When asked to calculate someone's share of a restaurant/bar cheque
(«выдели долю Кости», «сколько должна Вера»):

1. **Read every line item** with its price from the receipt.
2. **Clarify ambiguity by price, not by OCR'd name.** People refer to dishes
   by their real name while the receipt prints a different description. If a
   dish you don't see in the OCR output is named, find the line whose price
   matches and ask — don't silently substitute.
3. **Resolve shared items.** «половину X» splits that line's price in half.
   Drinks are typically per-person: «3 лимонада» means 3 × unit price goes to
   that person's share.
4. **Show the full line-item breakdown** in a table with the final sum, then
   wait for confirmation before any further action (e.g. creating a debt in
   Zen-Money).

## Command reference

- `zenmoney sync` — refresh the local cache (other commands do this anyway).
- `zenmoney tags` — categories with full paths ("Разное → Подарки").
- `zenmoney find --date YYYY-MM-DD --amount N [--payee STR] [--window DAYS]`
- `zenmoney show TXID`
- `zenmoney split TXID "Категория=сумма" ... [--comment STR]`
- `zenmoney create --date YYYY-MM-DD --amount N --payee STR --tag STR
  [--account STR] [--comment STR]`

## Pitfalls

- **Always check a transaction's currency before quoting its amount.** A
  transaction's `outcome`/`income` is denominated in the *account's* currency,
  not necessarily EUR. When you read the cache
  (`/data/hermes/zenmoney/state.json`), map
  `account[outcomeAccount].instrument → instrument[id].shortTitle` and report
  the currency with the figure. A large number on a weaker-currency account
  can be a small euro amount — reading it as euros is a mistake that was
  made and corrected once.
- **Don't invent a rule to explain a 400.** For a while `split` and `create`
  failed *every* time, because they sent 19 of the 30 fields a transaction has
  and the server answered `validationError: No value for property
  "reminderMarker"`. Two pitfalls were written here blaming that failure on
  `source: "split"` and on `hold: true`; both were wrong, and the real message
  was never read because the CLI discarded the response body. The CLI now
  reports what the server actually said — quote it verbatim instead of
  theorising.
- **`hold: true` is not an obstacle.** A pending (unsettled) transaction can
  be edited and split; the API accepts it, amount changes included. Worth
  mentioning only because the sync plugin rewrites the record when the payment
  settles, which can disturb a split made beforehand.
- **A `hold: true` record with several tags ≠ "not split yet".** When a pending
  transaction was already split in the app, the cache keeps BOTH the
  *original* unsettled record (hold=True, combined tags, full amount) AND
  separate child transactions that sum to it. Do not flag the hold record as
  "needs splitting" — first check for children whose amounts reconcile to the
  total; if they do, the split is already done.
- **`zenmoney sync` before analyzing — the cache goes stale.** A stale local
  cache once made the agent propose splits the owner had already done (reply:
  "почему ты видишь её не разбитой?"). Sync first, then list/flag.
- **CLI has no `update` command.** Available commands: `sync`, `tags`, `find`,
  `show`, `split`, `create`. You cannot change an existing transaction's
  category or amount — only split it or create a new one.
- **`zenmoney find` requires `--amount`.** It is mandatory. You cannot search
  by payee alone — always supply both `--date` and `--amount`.
- **A transaction may already be processed by hand.** If `find` returns no
  match for a known receipt, ask whether it was already split in the app
  before trying wider searches or `create`.
- **Receipt printing errors.** Some receipts have incorrect line totals
  (qty × price ≠ printed total). Trust the final total and VAT breakdown —
  they're the real charge. Recalculate each line as qty × price (rounded to
  2 decimals), discard the printed line total when it disagrees. Flag the
  discrepancy in the plan.
- **Tag names with a trailing space must be referenced by full path.** Some
  categories have a trailing space in their title. `resolve_tag` matches
  against `title.lower()` *without* stripping, so the bare name does NOT match
  the title with the space. Pass the full path instead — `zenmoney split TXID
  "Разное → Подарки=17.00" ...` — the path form is `_norm`-ed (stripped) and
  resolves correctly. Safest rule: always pass the full `parent → title` path
  from `zenmoney tags`, not the bare title.
- **Duplicate categories break `zenmoney split`.** If two tags share the same
  title+path, the CLI exits with `ambiguous tag '...', candidates: ...`. The
  owner must delete the duplicate in the Zen-Money app (Настройки →
  Категории), then re-run `zenmoney sync`.
- **CLI cannot create debt/loan transactions.** Only regular expense
  transactions via `zenmoney create`. When a split includes a «дал в долг»
  portion, split it into a temporary category (e.g. `Корректировка`) and ask
  the owner to create the debt transaction manually in the app.
- **`vision_analyze` can time out repeatedly — don't loop on it.** If it
  fails with "Request timed out" 2+ times, STOP retrying it. It is the vision
  backend, not the image. Report the outage and offer to retry later, or ask
  for the total and items as text. Do not install OCR packages or upscale
  images on a small server: image work once brought one to load 150.

## Guardrails

- **Never run `split` or `create` without explicit confirmation in this
  chat.** Present the full plan first, wait for the OK.
- **Category names come only from `zenmoney tags`.** Categories cannot be
  created from here; if the right one is missing, say so — the owner adds it in
  the Zen-Money app, then re-run `zenmoney tags`.
- Nothing is ever deleted — the CLI cannot delete, and neither may you.
- If the category sums don't reconcile with the transaction amount, show the
  discrepancy and ask; never force numbers to fit.
- If the CLI reports `ZENMONEY_TOKEN is not set`, relay that and ask for the
  token — do not retry or work around it.
- If `find` gives no confident match, ask instead of guessing.

## Extending this skill

As you learn merchant→category quirks (store names, recurring items, the
owner's corrections), propose them here so they compound. Edits go through
`/skills` review before they take effect.
