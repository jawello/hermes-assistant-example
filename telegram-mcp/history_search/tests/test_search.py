"""search_history's logic, against a fake Telethon client.

The fake answers like Telegram's search: matches newest first, below
offset_date, at most `limit`, by substring of the text.
"""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from history_search import core


# Telethon's entity classes, by name: core classifies by class name.
class User(SimpleNamespace):
    pass


class Chat(SimpleNamespace):
    pass


class Channel(SimpleNamespace):
    pass


ME = User(id=1, first_name="Alex", last_name="Sample", username="jawello")
masha = User(id=20002, first_name="Мария", last_name="Образцова", username=None)
PASHA = User(id=77, first_name="Павел", last_name="Петров", username=None)
HUMAN = Chat(id=30003, title="Club+ All")
NEWS = Channel(id=9, title="Новости", broadcast=True)
CLUB = Channel(id=8, title="Клуб", broadcast=False)


def utc(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def msg(mid, when, chat, chat_id, sender, text, out=False):
    return SimpleNamespace(id=mid, date=utc(when), chat=chat, chat_id=chat_id,
                           sender=sender, sender_id=getattr(sender, "id", None),
                           out=out, message=text)


masha_CHAT = [
    msg(10, "2024-11-10T12:00:00", masha, 20002, ME, "guitar fender for sale", out=True),
    msg(11, "2024-11-10T12:05:00", masha, 20002, masha, "guitar stratocaster"),
    msg(20, "2025-01-31T09:00:00", masha, 20002, masha, "усилители в магазине, статья"),
    msg(30, "2026-09-04T10:18:00", masha, 20002, ME, "Поехал платить за гитару", out=True),
]
GLOBAL = [
    msg(5801, "2026-09-30T09:01:00", HUMAN, -30003, PASHA, "Концерт 13"),
    msg(500, "2025-11-19T09:51:00", NEWS, -1000000000009, None, "клуб объявил концерт"),
    msg(400, "2023-03-03T13:40:00", CLUB, -1000000000008, PASHA, "концерт на 10 лет"),
]


class FakeClient:
    def __init__(self, by_entity=None, fail=None):
        self.by_entity = by_entity or {}
        self.fail = fail or {}
        self.calls = []

    def iter_messages(self, entity, search=None, from_user=None, offset_date=None, limit=None):
        self.calls.append({"entity": entity, "search": search, "from_user": from_user,
                           "offset_date": offset_date, "limit": limit})
        return self._gen(entity, search, from_user, offset_date, limit)

    async def _gen(self, entity, search, from_user, offset_date, limit):
        if search in self.fail:
            raise self.fail[search]
        pool = self.by_entity.get(None if entity is None else entity.id, [])
        hits = sorted((m for m in pool if search.lower() in m.message.lower()),
                      key=lambda m: m.date, reverse=True)
        n = 0
        for m in hits:
            if offset_date and m.date >= offset_date:
                continue
            if from_user is not None and m.sender_id != from_user.id:
                continue
            if limit is not None and n >= limit:
                return
            n += 1
            yield m


async def resolve(identifier, client=None):
    table = {20002: masha, "@masha": masha, 77: PASHA, -30003: HUMAN}
    if identifier in table:
        return table[identifier]
    raise ValueError(f"Cannot find any entity corresponding to {identifier!r}")


def run(client, **kw):
    return asyncio.run(core.search(client, resolve, **kw))


def masha_client():
    return FakeClient({20002: masha_CHAT})


def global_client(**kw):
    return FakeClient({None: GLOBAL}, **kw)


# --- merge ----------------------------------------------------------------

def test_two_queries_hitting_one_message_give_one_row():
    out = run(masha_client(), queries=["guitar", "for sale"], chat_id=20002)
    row = next(r for r in out["results"] if r["message_id"] == 10)
    assert row["matched"] == ["guitar", "for sale"]
    assert out["total"] == 2
    assert out["per_query"] == {"guitar": 2, "for sale": 1}


def test_duplicate_queries_searched_once():
    client = masha_client()
    run(client, queries=["Кипр", " Кипр "], chat_id=20002)
    assert [c["search"] for c in client.calls] == ["Кипр"]


# --- order ----------------------------------------------------------------

def test_oldest_returns_the_first_mention():
    out = run(masha_client(), queries=["guitar", "усилители", "гитар"],
              chat_id=20002, order="oldest", limit=1)
    assert [r["message_id"] for r in out["results"]] == [10]
    assert out["total"] == 4 and out["returned"] == 1


def test_newest_returns_the_latest():
    out = run(masha_client(), queries=["guitar", "гитар"], chat_id=20002, limit=2)
    assert [r["message_id"] for r in out["results"]] == [30, 11]


def test_truncated_when_a_query_reaches_the_scan_cap():
    out = run(masha_client(), queries=["guitar"], chat_id=20002, scan_cap=1)
    assert out["truncated"] is True
    out = run(masha_client(), queries=["guitar"], chat_id=20002, scan_cap=5)
    assert out["truncated"] is False


def test_not_truncated_when_reading_stopped_at_since():
    old = msg(3, "2023-02-01T10:00:00", masha, 20002, masha, "guitar rent")
    client = FakeClient({20002: [old] + masha_CHAT})
    # Three matches read, the third older than since: reading stopped there.
    out = run(client, queries=["guitar"], chat_id=20002, since="2024-11-10", scan_cap=3)
    assert out["truncated"] is False
    out = run(client, queries=["guitar"], chat_id=20002, scan_cap=3)
    assert out["truncated"] is True


# --- dates ----------------------------------------------------------------

def test_dates_are_andreis_days_inclusive():
    # 22:30 UTC on 09.11 is already 10.11 in Nicosia (UTC+2 in November).
    late = msg(5, "2024-11-09T22:30:00", masha, 20002, masha, "guitar early")
    client = FakeClient({20002: [late] + masha_CHAT})
    out = run(client, queries=["guitar"], chat_id=20002,
              since="2024-11-10", until="2024-11-10")
    assert sorted(r["message_id"] for r in out["results"]) == [5, 10, 11]
    # until's day is inclusive: the search starts at 11.11 00:00 Nicosia.
    assert client.calls[0]["offset_date"] == utc("2024-11-10T22:00:00")


def test_since_after_until():
    out = run(masha_client(), queries=["guitar"], since="2025-01-02", until="2025-01-01")
    assert "since" in out["error"]


def test_a_bad_date_is_refused():
    out = run(masha_client(), queries=["guitar"], since="10.11.2024")
    assert "YYYY-MM-DD" in out["error"]


# --- scope ----------------------------------------------------------------

def test_with_chat_id_the_search_runs_in_that_chat():
    client = masha_client()
    run(client, queries=["guitar"], chat_id=20002)
    assert client.calls[0]["entity"] is masha


def test_without_chat_id_it_is_global_and_drops_broadcast_channels():
    client = global_client()
    out = run(client, queries=["концерт"])
    assert client.calls[0]["entity"] is None
    assert sorted(r["message_id"] for r in out["results"]) == [400, 5801]
    out = run(global_client(), queries=["концерт"], include_channels=True)
    assert sorted(r["message_id"] for r in out["results"]) == [400, 500, 5801]


def test_from_user_reaches_telegram_and_filters():
    client = global_client()
    out = run(client, queries=["концерт"], from_user=77)
    assert client.calls[0]["from_user"] is PASHA
    assert {r["sender"] for r in out["results"]} == {"Павел Петров"}


def test_an_unknown_chat_is_an_error():
    out = run(masha_client(), queries=["x"], chat_id="nobody")
    assert "cannot find" in out["error"]


# --- rows -----------------------------------------------------------------

def test_rows_are_short_and_complete():
    long = msg(40, "2025-02-01T10:00:00", masha, 20002, ME, "гитара " + "я" * 400, out=True)
    out = run(FakeClient({20002: [long]}), queries=["гитара"], chat_id=20002)
    row = out["results"][0]
    assert len(row["text"]) == core.TEXT_CHARS + 1 and row["text"].endswith("…")
    assert row == {**row, "chat_id": 20002, "chat": "Мария Образцова", "chat_type": "User",
                   "message_id": 40, "date": "2025-02-01T10:00:00+00:00",
                   "sender": "Alex Sample", "sender_id": 1, "out": True,
                   "matched": ["гитара"]}


def test_group_row_names_chat_and_sender():
    out = run(global_client(), queries=["Концерт 13"])
    row = out["results"][0]
    assert (row["chat"], row["chat_type"], row["sender"]) == ("Club+ All", "Group", "Павел Петров")


def test_message_without_chat_entity():
    lost = msg(600, "2026-01-01T10:00:00", None, -5, PASHA, "концерт где-то")
    out = run(FakeClient({None: [lost]}), queries=["концерт"])
    row = out["results"][0]
    assert row["chat"] is None and row["chat_type"] == "Unknown"


# --- errors ---------------------------------------------------------------

class FloodWaitError(Exception):
    pass


def test_a_failing_query_keeps_the_others_and_is_not_retried():
    client = global_client(fail={"оглашение": FloodWaitError("A wait of 30 seconds is required")})
    out = run(client, queries=["оглашение", "концерт"])
    assert out["errors"] == [{"query": "оглашение",
                              "error": "FloodWaitError: A wait of 30 seconds is required"}]
    assert out["per_query"] == {"концерт": 2}
    assert [c["search"] for c in client.calls] == ["оглашение", "концерт"]


def test_every_query_failing_is_an_error():
    client = global_client(fail={"x": RuntimeError("boom")})
    out = run(client, queries=["x"])
    assert "error" in out and out["errors"][0]["query"] == "x"


# --- arguments ------------------------------------------------------------

def test_empty_and_too_many_queries_are_refused():
    assert "error" in run(masha_client(), queries=[])
    assert "error" in run(masha_client(), queries=["  "])
    assert "error" in run(masha_client(), queries=list("abcdef"))


def test_a_single_string_is_one_query():
    out = run(masha_client(), queries="guitar", chat_id=20002)
    assert out["per_query"] == {"guitar": 2}


def test_limit_is_capped_at_100():
    client = masha_client()
    out = run(client, queries=["guitar"], chat_id=20002, limit=1000)
    assert out["returned"] == 2
    assert client.calls[0]["limit"] == core.SCAN_CAP


def test_limit_as_string_and_garbage():
    out = run(masha_client(), queries=["guitar"], chat_id=20002, limit="1")
    assert out["returned"] == 1
    assert "limit" in run(masha_client(), queries=["guitar"], limit="many")["error"]


def test_unknown_order_is_refused():
    assert "order" in run(masha_client(), queries=["x"], order="random")["error"]
