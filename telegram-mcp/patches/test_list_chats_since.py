"""Tests for the list_chats `since` patch.

The patch is applied, after list_chats_last_message.py as in the Dockerfile,
to a verbatim copy of upstream's list_chats (fixtures/), and the patched
function is run against a fake Telethon client: it must return only the chats
that moved at or after `since`, and leave every other filter as upstream
wrote it.
"""
import asyncio
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import list_chats_last_message
import list_chats_since as patch

UPSTREAM = (Path(__file__).parent / "fixtures" / "list_chats_v3.2.65.py").read_text()
NOW = datetime(2026, 10, 5, 18, 30, tzinfo=timezone.utc)


class FakeClient:
    def __init__(self, dialogs):
        self.dialogs = dialogs
        self.asked = None

    async def get_dialogs(self, limit, archived):
        self.asked = {"limit": limit, "archived": archived}
        return self.dialogs[:limit]


def dialog(chat_id, minutes_ago, muted=False, archived=False):
    """A user dialog whose newest message is `minutes_ago` before NOW."""
    date = None if minutes_ago is None else NOW - timedelta(minutes=minutes_ago)
    mute_until = time.time() + 3600 if muted else None
    return SimpleNamespace(
        entity=SimpleNamespace(id=chat_id, first_name=f"user{chat_id}", last_name=None),
        date=date,
        archived=archived,
        unread_count=0,
        dialog=SimpleNamespace(unread_mark=False, notify_settings=SimpleNamespace(mute_until=mute_until)),
    )


def load(source, client):
    async def ensure_connected(cl):
        return None

    namespace = {
        "datetime": datetime,
        "time": time,
        "BotMethodInvalidError": type("BotMethodInvalidError", (Exception,), {}),
        "get_client": lambda account: client,
        "ensure_connected": ensure_connected,
        "is_chat_allowlist_enabled": lambda: False,
        "is_chat_allowed": lambda *a: True,
        "get_marked_id": lambda entity: entity.id,
        "get_entity_filter_type": lambda entity: "user",
        "get_entity_type": lambda entity: "User",
        "sanitize_name": lambda name: name,
        "format_tool_result": lambda records: records,
        "log_and_format_error": lambda name, e, **kw: f"-ERR- {e!r}",
    }
    exec(source, namespace)
    return namespace["list_chats"]


def patched():
    return patch.patched_chats(list_chats_last_message.patched_chats(UPSTREAM))


def run(client, **kwargs):
    return asyncio.run(load(patched(), client)(**kwargs))


def ids(records):
    return [r["chat_id"] for r in records]


def an_hour_ago():
    return (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_only_chats_that_moved_after_since_come_back():
    client = FakeClient([dialog(1, 5), dialog(2, 59), dialog(3, 61), dialog(4, 600)])
    assert ids(run(client, limit=200, since=an_hour_ago())) == [1, 2]


def test_a_chat_that_moved_exactly_at_since_counts():
    client = FakeClient([dialog(1, 60), dialog(2, 61)])
    assert ids(run(client, limit=200, since=an_hour_ago())) == [1]


def test_records_still_carry_last_message_at():
    out = run(FakeClient([dialog(1, 5)]), limit=200, since=an_hour_ago())
    assert out[0]["last_message_at"] == (NOW - timedelta(minutes=5)).isoformat()


def test_a_chat_without_a_date_is_kept():
    # Rather read one chat too many than drop one quietly.
    client = FakeClient([dialog(1, None), dialog(2, 600)])
    assert ids(run(client, limit=200, since=an_hour_ago())) == [1]


def test_nothing_new_says_so():
    out = run(FakeClient([dialog(1, 90)]), limit=200, since=an_hour_ago())
    assert out == "No chats found matching the criteria."


def test_since_keeps_the_other_filters():
    client = FakeClient([dialog(1, 5, muted=True), dialog(2, 5), dialog(3, 5, archived=True)])
    out = run(client, limit=200, archived=False, unmuted_only=True, since=an_hour_ago())
    assert ids(out) == [2]
    assert client.asked == {"limit": 200, "archived": False}


def test_a_since_without_a_zone_is_utc():
    naive = (NOW - timedelta(hours=1)).replace(tzinfo=None).isoformat()
    client = FakeClient([dialog(1, 5), dialog(2, 61)])
    assert ids(run(client, limit=200, since=naive)) == [1]


def test_a_bad_since_is_refused_before_telegram_is_asked():
    client = FakeClient([dialog(1, 5)])
    out = run(client, limit=200, since="yesterday")
    assert out.startswith("Invalid since format")
    assert client.asked is None


def test_without_since_upstream_behaviour_is_unchanged():
    client = FakeClient([dialog(1, 5), dialog(2, 600)])
    assert ids(run(client, limit=200)) == [1, 2]


def test_the_tool_documents_since():
    out = patched()
    assert "    since: str = None,\n" in out
    assert "        since: " in out


def test_patching_twice_changes_nothing():
    once = patched()
    assert patch.patched_chats(once) == once


def test_a_file_without_the_anchors_is_refused():
    with pytest.raises(patch.PatchError):
        patch.patched_chats(UPSTREAM.replace("            dialogs = await cl.get_dialogs(limit=limit, archived=archived)\n", ""))


def test_a_file_with_two_list_chats_is_refused():
    with pytest.raises(patch.PatchError):
        patch.patched_chats(UPSTREAM + UPSTREAM)


def test_main_patches_the_file_in_place(tmp_path):
    chats = tmp_path / "chats.py"
    chats.write_text(UPSTREAM)
    assert patch.main(["prog", str(chats)]) == 0
    assert "    since: str = None,\n" in chats.read_text()
