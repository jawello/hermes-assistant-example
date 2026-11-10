"""High-level operations over the diff API + local state."""
import time
import uuid
from datetime import date as _date
from decimal import Decimal

from zenmoney_cli.api import ZenMoneyClient
from zenmoney_cli.state import ENTITY_TYPES, State

CENT = Decimal("0.01")


SEP = " → "

# A split part is a record we invent, so it must not claim to be the bank's
# operation: two transactions sharing one bank id would confuse the sync
# plugin, which matches on it to update the transaction once it settles.
PART_RESET = {
    "incomeBankID": None, "outcomeBankID": None, "source": None,
    "qrCode": None, "reminderMarker": None, "hold": False,
}


def _norm(name: str) -> str:
    parts = [p.strip().lower()
             for p in name.replace("/", "→").split("→") if p.strip()]
    return SEP.join(parts)


def tag_path(state: State, tag_id: str) -> str:
    parts, seen = [], set()
    cur = state.entities["tag"].get(tag_id)
    while cur is not None and cur["id"] not in seen:
        seen.add(cur["id"])
        parts.append(cur["title"])
        cur = state.entities["tag"].get(cur.get("parent") or "")
    return SEP.join(reversed(parts))


def list_tags(state: State) -> list[dict]:
    tags = [{"id": t["id"], "title": t["title"],
             "path": tag_path(state, t["id"])}
            for t in state.entities["tag"].values()]
    return sorted(tags, key=lambda t: t["path"])


def resolve_tag(state: State, name: str) -> str:
    wanted = _norm(name)
    tags = list_tags(state)
    hits = [t for t in tags
            if _norm(t["path"]) == wanted or t["title"].lower() == wanted]
    if len(hits) == 1:
        return hits[0]["id"]
    if len(hits) > 1:
        raise ValueError("ambiguous tag %r, candidates: %s"
                         % (name, "; ".join(t["path"] for t in hits)))
    near = [t["path"] for t in tags
            if wanted.split(SEP)[-1] in t["path"].lower()]
    raise ValueError("unknown tag %r%s" % (
        name, ", did you mean: " + "; ".join(near) if near else ""))


def find(state: State, date: str, amount: Decimal,
         payee: str | None = None, window: int = 3) -> list[dict]:
    target_day = _date.fromisoformat(date)
    amount = Decimal(amount).quantize(CENT)
    results = []
    for tx in state.entities["transaction"].values():
        if tx.get("deleted") or tx.get("income", 0) or not tx.get("outcome"):
            continue
        day = _date.fromisoformat(tx["date"])
        day_off = abs((day - target_day).days)
        if day_off > window:
            continue
        outcome = Decimal(str(tx["outcome"])).quantize(CENT)
        if abs(outcome - amount) < Decimal("0.005"):
            match = "exact"
        elif amount and abs(outcome - amount) / amount <= Decimal("0.1"):
            match = "near"
        else:
            continue
        hay = " ".join(filter(None, [tx.get("payee"),
                                     tx.get("originalPayee")])).lower()
        payee_hit = bool(payee) and payee.lower() in hay
        results.append({
            "id": tx["id"], "date": tx["date"], "payee": tx.get("payee"),
            "outcome": float(outcome), "match": match,
            "payee_hit": payee_hit, "_day_off": day_off,
            "tags": [tag_path(state, t) for t in (tx.get("tag") or [])],
        })
    results.sort(key=lambda r: (r["match"] != "exact",
                                not r["payee_hit"], r["_day_off"]))
    for r in results:
        del r["_day_off"]
    return results


def _split_original_currency(tx: dict, amounts: list[Decimal],
                             outcome: Decimal) -> list[Decimal | None]:
    """Divide `opOutcome` in step with `outcome`, or return all-None.

    `opOutcome` is the sum that actually left the card when it was charged in
    another currency; it is meaningless without `opOutcomeInstrument`. Leaving
    it at the parent's full value while shrinking `outcome` would silently
    desync the two.
    """
    if tx.get("opOutcomeInstrument") is None or not tx.get("opOutcome"):
        return [None] * len(amounts)
    total = Decimal(str(tx["opOutcome"]))
    shares = [(total * a / outcome).quantize(CENT) for a in amounts]
    # The rounding residual lands on the original, so the parts still add up
    # to exactly what the bank charged.
    shares[0] = total - sum(shares[1:])
    return shares


def split(client: ZenMoneyClient, state: State, tx_id: str,
          parts: list[tuple[str, Decimal]],
          comment: str | None = None,
          shares: list[tuple[str, Decimal]] | None = None) -> dict:
    """Split a bank payment into categorized parts and other people's shares.

    A share is someone else's part of a shared purchase: no category, the
    person in `payee`, so the user can convert it into a debt in the app. The
    first categorized part stays the bank's own transaction.
    """
    shares = shares or []
    if not parts:
        raise ValueError("split needs a categorized part first (the user's "
                         "own share); shares come after it")
    tx = state.entities["transaction"].get(tx_id)
    if tx is None or tx.get("deleted"):
        raise ValueError(f"no transaction {tx_id!r} in local state (run sync?)")
    if tx.get("income", 0):
        raise ValueError("refusing to split: transaction has an income side")
    if len(parts) + len(shares) < 2:
        raise ValueError("split needs at least 2 parts")
    if any(not name.strip() for name, _ in shares):
        raise ValueError("every share needs a person's name")
    amounts = [Decimal(a).quantize(CENT) for _, a in parts + shares]
    if any(a <= 0 for a in amounts):
        raise ValueError("every part must be > 0")
    outcome = Decimal(str(tx["outcome"])).quantize(CENT)
    if sum(amounts) != outcome:
        raise ValueError(
            f"parts sum {sum(amounts)} != transaction outcome {outcome}")
    tag_ids = [resolve_tag(state, name) for name, _ in parts]
    # A share has no category, and its payee is the person, not the shop.
    tag_ids += [None] * len(shares)
    payees = [None] * len(parts) + [name.strip() for name, _ in shares]

    op_shares = _split_original_currency(tx, amounts, outcome)

    now = int(time.time())
    # Build every part off the parent so it carries the server's full field
    # set. The hand-built dict this replaces sent 19 of the 30 fields the
    # server stores, which is the likeliest cause of the 400s on split.
    modified = dict(tx, outcome=float(amounts[0]), tag=[tag_ids[0]],
                    changed=now)
    if comment:
        modified["comment"] = comment
    batch = [modified]
    for tag_id, payee, amount, op in zip(tag_ids[1:], payees[1:], amounts[1:],
                                         op_shares[1:]):
        part = dict(tx, **PART_RESET)
        part.update(id=str(uuid.uuid4()), created=now, changed=now,
                    deleted=False, viewed=True, outcome=float(amount),
                    tag=[tag_id] if tag_id else None, comment=comment)
        if payee:
            part.update(payee=payee, merchant=None, originalPayee=None,
                        comment=f"{payee} — {comment}" if comment else payee)
        if op is not None:
            part["opOutcome"] = float(op)
        batch.append(part)
    if op_shares[0] is not None:
        modified["opOutcome"] = float(op_shares[0])
    resp = client.diff(state.server_timestamp, transaction=batch)
    state.apply(resp)
    # The server may not echo our objects back; make sure the cache holds them.
    for obj in batch:
        state.entities["transaction"].setdefault(obj["id"], obj)
    state.entities["transaction"][tx_id] = modified
    state.save()
    return {"ok": True, "original": tx_id,
            "parts": [{"id": b["id"],
                       "tag": tag_path(state, b["tag"][0]) if b["tag"] else None,
                       "payee": b.get("payee"), "amount": b["outcome"]}
                      for b in batch]}


def _pick_account(state: State, account: str | None) -> dict:
    accounts = [a for a in state.entities["account"].values()
                if not a.get("deleted")]
    if account:
        hits = [a for a in accounts
                if a.get("title", "").lower() == account.lower()]
        if len(hits) != 1:
            raise ValueError("unknown account %r, have: %s" % (
                account, "; ".join(a.get("title", "?") for a in accounts)))
        return hits[0]
    cards = [a for a in accounts if a.get("type") == "ccard"]
    if len(cards) != 1:
        raise ValueError("cannot pick account automatically, pass --account; "
                         "have: " + "; ".join(a.get("title", "?")
                                              for a in accounts))
    return cards[0]


def create(client: ZenMoneyClient, state: State, date: str, amount: Decimal,
           payee: str, tag: str, account: str | None = None,
           comment: str | None = None) -> dict:
    users = list(state.entities["user"].values())
    if len(users) != 1:
        raise ValueError("no user in local state — run sync first")
    acc = _pick_account(state, account)
    tag_id = resolve_tag(state, tag)
    amount = Decimal(amount).quantize(CENT)
    if amount <= 0:
        raise ValueError("amount must be > 0")
    now = int(time.time())
    obj = {
        "id": str(uuid.uuid4()), "user": users[0]["id"],
        "created": now, "changed": now, "deleted": False, "hold": False,
        "viewed": True,
        "income": 0, "incomeAccount": acc["id"],
        "incomeInstrument": acc["instrument"],
        "outcome": float(amount), "outcomeAccount": acc["id"],
        "outcomeInstrument": acc["instrument"],
        "tag": [tag_id], "date": date, "payee": payee,
        "originalPayee": None, "merchant": None, "comment": comment,
        # The server stores these on every transaction; this object used to
        # omit them, the same gap that `split` had.
        "opIncome": 0, "opIncomeInstrument": None,
        "opOutcome": 0, "opOutcomeInstrument": None,
        # Hand-made, so it belongs to no bank operation and no sync plugin.
        "incomeBankID": None, "outcomeBankID": None, "source": None,
        "qrCode": None, "reminderMarker": None,
        "latitude": None, "longitude": None,
    }
    resp = client.diff(state.server_timestamp, transaction=[obj])
    state.apply(resp)
    state.entities["transaction"].setdefault(obj["id"], obj)
    state.save()
    return {"ok": True, "id": obj["id"], "amount": float(amount),
            "tag": tag_path(state, tag_id)}


def sync(client: ZenMoneyClient, state: State) -> dict:
    resp = client.diff(state.server_timestamp)
    state.apply(resp)
    state.save()
    return {
        "serverTimestamp": state.server_timestamp,
        "fetched": {t: len(resp.get(t, []) or []) for t in ENTITY_TYPES
                    if resp.get(t)},
    }
