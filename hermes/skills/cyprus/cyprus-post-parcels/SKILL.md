---
name: cyprus-post-parcels
description: Receive international parcels via Cyprus Post — verify the ThalisUpp customs platform, know the import-duty thresholds, and prepare a post-office pickup task.
version: 2
---

# Cyprus Post — incoming parcels & customs

Trigger: a "New Package: <tracking>" notification from Cyprus Post
(`info@cypruspost.post`), or a question about collecting a parcel / customs /
duties on an incoming international item.

## ThalisUpp — what it is (verify, then explain)

- `cypruspost.post/en/thalisupp` is the **official** Cyprus Post customs
  platform. It is legit, not a scam: `.post` is a TLD reserved for postal
  operators, and the mail comes from `info@cypruspost.post`.
- Purpose: upload customs documents (proof of purchase) and/or pay import
  duties / VAT / taxes **before** the item arrives, to speed up delivery or
  collection.
- **Registration and use are free.** Payment is optional — duties can also be
  paid at the post office on collection.
- It needs a **personal account** (Login / register on cypruspost.post). Info
  about a specific item is visible only after logging in and entering the
  tracking number — and only if Customs already calculated charges or requested
  documents.

## The "New Package" email is not a payment demand

A "New Package: <tracking>" mail just registers the item to the addressee. It
does NOT mean charges are due or the item has arrived in Cyprus. It says to log
in to "check whether" docs/duties are needed. Receiving it ≠ item is here.

## The email never names the sender or contents

The "New Package" mail carries only the barcode + the origin postal operator.
There is no sender, item description or value, so the parcel **cannot be
identified from memory/mail alone** — don't try to match it to a known order by
the tracking suffix. Details (charges, requested documents) are visible only
inside ThalisUpp after logging in and entering the tracking number.

## Confirming the account (first-time blocker)

Before ThalisUpp shows anything, the Cyprus Post account must be activated via
the «Confirm Account» email (it arrives alongside the "New Package"
notifications). Until that link is clicked, no parcel is visible.

## Granting the assistant access

The assistant logs in only via `browser_login` against the credential vault
(Vaultwarden), matched by exact hostname. To enable it:
1. In the Vaultwarden web UI, add a **Login** entry bound to `cypruspost.post`
   (or `www.cypruspost.post` — hostname must match exactly).
2. Username = the Cyprus Post login; password = the account password.
3. Tell the assistant "added" → it runs `browser_login(url="https://www.cypruspost.post/en/login")`
   and reads ThalisUpp itself.

The assistant **cannot** add vault entries itself (no `bw` CLI / docker access),
so this is a user-side step — never ask for the password in chat.

## Logging in & reading ThalisUpp (verified account URLs)

`browser_login` on the bare domain fails with `reason: "login form not found"`
— the login form is NOT on the homepage. The working URLs:

- **Login form:** `https://www.cypruspost.post/en/login` — this is what you pass
  to `browser_login`. A successful login returns `ok: true` + the username.
- **Account details:** `https://www.cypruspost.post/en/account`. The header
  flips from `Login` to `Account | Logout` once authenticated (that's the
  logged-in signal).
- **ThalisUpp package list:** `https://www.cypruspost.post/en/account/postal-packages`
  (the "THALISUPP" link in the account nav). Lists tracking number + date +
  status per parcel.
- **Package details:** `https://www.cypruspost.post/en/postal-package-details?uid=<TRACKING>`
  — status, description, and the duty breakdown (VAT / Presentation-to-customs
  fee / Temp-Imp) with a payment row.
- **Payment details:** `https://www.cypruspost.post/en/postal-package-payment-details?uid=<PAYMENT-ID>`

### Multi-tab gotcha

`browser_login` succeeds, but `browser_snapshot` / `browser_navigate` stay
pinned to a **stale tab** (a leftover tab from an earlier task), so they keep
showing the wrong page. Find the real cypruspost tab with
`browser_cdp` → `Target.getTargets` (look for a `page` target whose url is
`www.cypruspost.post`), then drive that `targetId` directly via `browser_cdp`
`Runtime.evaluate` (pass `target_id`): navigate with
`location.href = '...'` and read state with
`(document.body.innerText||'').replace(/\s+/g,' ')`.

### Reading the duty status

`CLEARED` means customs has processed the item — but it does **not** mean zero
duty. On the package-details page, check the payment section: a parcel can be
`CLEARED` and still list VAT + Presentation fee + Temp-Imp with a payment row
that says `APPROVED` (already paid) or a payable amount. Only a cleared
*and* no-outstanding-payment parcel is truly "nothing to pay".

## Import thresholds (what to expect to pay)

| Origin / value | Payable |
|---|---|
| Genuine EU origin (e.g. NL) | nothing — no duty/VAT |
| Non-EU, value < €150 | nothing |
| Non-EU, €150–€1000 | VAT 19% |
| Non-EU, > €1000 | duty + VAT |

- Cyprus standard VAT is 19%.
- The tracking suffix (e.g. `NL`) only names the postal operator (PostNL), not
  the true origin — Chinese marketplace parcels often route via NL/BE, so
  confirm the real country of origin before assuming "no duty".
- A missing/incomplete CN22/CN23 declaration → Cyprus Post may ask the
  recipient to upload proof of purchase (invoice/receipt) via ThalisUpp. That
  upload is free.

## Post office pickup

- All Cyprus Post offices: **Mon–Fri 08:00–14:30**, closed weekends and public
  holidays.
- The owner's local branch (address, phone, Maps pin) lives in memory, not in
  this skill: `hindsight_recall` "почтовое отделение".
- Full branch list + hours: https://www.cypruspost.post/en/post-office-locations

## Pickup task procedure

1. Search recent Cyprus Post mail for an actual collection notice, not just a
   `New Package` registration; read it for the exact tracking number, branch,
   postal reference and last collection date. Check the tracking status before
   saying the parcel is ready: customs clearance or payment alone does not
   establish arrival at the pickup point.
2. Cross-reference the tracking number with an order confirmation or prior
   correspondence before naming the contents. Do not infer contents from the
   barcode suffix or a customs payment; when the match is not confirmed, leave
   the contents unidentified. Distinguish the tracking number from a postal
   reference and from a payment receipt identifier.
3. Search TickTick for the exact tracking number and inspect any likely existing
   pickup task before creating one. Update an existing task if it is the same
   collection, so there are no duplicate errands.
4. Create an Inbox errand with the carrier and tracking number in the title;
   put the notice's collection deadline, postal reference, verified contents
   (if any), branch/address, hours, ID requirement and a Google Maps pin in the
   content. Always put the parcel's pickup PIN there too, read exactly from the
   original message or image. Login or verification codes never go into a task.
   Unless a visit time was given, do not schedule a time; check tracking again
   before going. Follow the TickTick skill's date-only rules if assigning a due
   date, and read the created task back to verify.
