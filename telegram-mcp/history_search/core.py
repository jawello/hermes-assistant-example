"""Search over the user's whole Telegram history: several words, dates, either order.

Telegram's own search reaches the whole history and handles Russian word
endings, but it takes one word per call, returns the newest matches first and,
through upstream's tools, whole messages. This module runs up to five words,
merges the hits, sorts them oldest or newest first and returns short rows.

The Telegram client and the resolver are parameters, so this module needs
neither Telethon nor upstream's server to be tested; tools.py wires it to
upstream's `mcp`.
Design: design notes not published
"""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Nicosia")  # the user's days
MAX_QUERIES = 5
MAX_LIMIT = 100
# Telegram searches newest first only. Every query is read up to this many
# matches, then sorted here; `truncated` says when one query had more.
SCAN_CAP = 1000
TEXT_CHARS = 300
ORDERS = ("newest", "oldest")


class ArgError(ValueError):
    """An argument the agent can fix and retry with."""


def _day(value, name):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ArgError(f"{name} must be a date YYYY-MM-DD, got {value!r}") from None


def bounds(since, until):
    """the user's days → UTC [lower, upper): both days inclusive."""
    lower = upper = None
    if since:
        lower = datetime.combine(_day(since, "since"), time(), TZ).astimezone(timezone.utc)
    if until:
        next_day = _day(until, "until") + timedelta(days=1)
        upper = datetime.combine(next_day, time(), TZ).astimezone(timezone.utc)
    if lower and upper and lower >= upper:
        raise ArgError("since is after until")
    return lower, upper


def clean_queries(queries):
    if isinstance(queries, str):
        queries = [queries]
    out = []
    for q in queries or []:
        q = str(q).strip()
        if q and q.lower() not in (seen.lower() for seen in out):
            out.append(q)
    if not out:
        raise ArgError("give at least one word to search for")
    if len(out) > MAX_QUERIES:
        raise ArgError(f"at most {MAX_QUERIES} queries per call, got {len(out)}")
    return out


def clean_limit(limit):
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ArgError(f"limit must be a number, got {limit!r}") from None
    return max(1, min(limit, MAX_LIMIT))


# --- entities -------------------------------------------------------------

def _cls(entity) -> str:
    return type(entity).__name__


def chat_type(entity) -> str:
    name = _cls(entity)
    if name.startswith("Channel"):
        return "Channel" if getattr(entity, "broadcast", False) else "Supergroup"
    if name.startswith("Chat"):
        return "Group"
    if name.startswith("User"):
        return "User"
    return "Unknown"


def display_name(entity):
    if entity is None:
        return None
    title = getattr(entity, "title", None)
    if title:
        return title
    name = " ".join(p for p in (getattr(entity, "first_name", None),
                                getattr(entity, "last_name", None)) if p)
    return name or getattr(entity, "username", None)


def short(text) -> str:
    text = text or ""
    return text if len(text) <= TEXT_CHARS else text[:TEXT_CHARS] + "…"


def row(m) -> dict:
    return {
        "chat_id": m.chat_id,
        "chat": display_name(m.chat),
        "chat_type": chat_type(m.chat),
        "message_id": m.id,
        "date": m.date.astimezone(timezone.utc).isoformat(),
        "sender": display_name(m.sender),
        "sender_id": m.sender_id,
        "out": bool(m.out),
        "text": short(m.message),
        "matched": [],
    }


# --- search ---------------------------------------------------------------

async def search(client, resolve, queries, chat_id=None, from_user=None, since=None,
                 until=None, order="newest", limit=30, include_channels=False,
                 scan_cap=SCAN_CAP) -> dict:
    try:
        queries = clean_queries(queries)
        lower, upper = bounds(since, until)
        if order not in ORDERS:
            raise ArgError(f"order must be one of: {', '.join(ORDERS)}")
        limit = clean_limit(limit)
    except ArgError as e:
        return {"error": str(e)}
    try:
        entity = await resolve(chat_id, client) if chat_id is not None else None
        sender = await resolve(from_user, client) if from_user is not None else None
    except Exception as e:
        return {"error": f"cannot find the chat or person: {e}"}

    hits, per_query, errors, truncated = {}, {}, [], False
    for q in queries:
        found = scanned = 0
        try:
            async for m in client.iter_messages(entity, search=q, from_user=sender,
                                                offset_date=upper, limit=scan_cap):
                scanned += 1
                if lower and m.date < lower:
                    break
                if upper and m.date >= upper:
                    continue
                if sender is not None and m.sender_id != sender.id:
                    continue
                if entity is None and not include_channels and chat_type(m.chat) == "Channel":
                    continue
                found += 1
                key = (m.chat_id, m.id)
                hits.setdefault(key, row(m))["matched"].append(q)
            else:
                truncated = truncated or scanned >= scan_cap
        except Exception as e:  # FloodWait and friends: report, never retry here
            errors.append({"query": q, "error": f"{type(e).__name__}: {e}"})
            continue
        per_query[q] = found

    if errors and not per_query:
        return {"error": "every search failed", "errors": errors}
    rows = sorted(hits.values(), key=lambda r: (r["date"], r["message_id"]),
                  reverse=(order == "newest"))[:limit]
    result = {"total": len(hits), "returned": len(rows), "truncated": truncated,
              "per_query": per_query, "results": rows}
    if errors:
        result["errors"] = errors
    return result
