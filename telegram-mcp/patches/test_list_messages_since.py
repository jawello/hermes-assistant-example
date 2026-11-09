"""Tests for the list_messages `since` patch.

The patch is applied to a verbatim copy of upstream's list_messages
(fixtures/), and the patched function is run against a fake Telethon client:
it must return only the messages after `since`, newest first, stop reading
at the first older one, and leave every other filter as upstream wrote it.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import list_messages_since as patch

UPSTREAM = (Path(__file__).parent / "fixtures" / "list_messages_v3.2.65.py").read_text()
NOW = datetime(2026, 10, 5, 18, 30, tzinfo=timezone.utc)


class FakeClient:
    """Telethon's iter_messages without filters: newest -> oldest."""

    def __init__(self, messages):
        self.messages = sorted(messages, key=lambda m: m.date, reverse=True)
        self.read = 0

    async def iter_messages(self, entity, **params):
        assert not params, f"since reads unfiltered, got {params}"
        for msg in self.messages:
            self.read += 1
            yield msg


def chat(minutes_ago):
    """One message per entry, `minutes_ago` before NOW; id = position."""
    return [
        SimpleNamespace(id=i, date=NOW - timedelta(minutes=m))
        for i, m in enumerate(minutes_ago, 1)
    ]


def load(source, client):
    async def resolve_entity(chat_id, cl):
        return chat_id

    async def prefetch_transcripts(*args):
        return None

    namespace = {
        "Union": __import__("typing").Union,
        "datetime": datetime,
        "timedelta": timedelta,
        "get_client": lambda account: client,
        "resolve_entity": resolve_entity,
        "get_marked_id": lambda entity: entity,
        "transcription": SimpleNamespace(prefetch_transcripts=prefetch_transcripts),
        "message_to_dict": lambda msg, chat_id: msg.id,
        "format_tool_result": lambda records: records,
        "log_and_format_error": lambda name, e, **kw: f"-ERR- {e!r}",
    }
    exec(source, namespace)
    return namespace["list_messages"]


def run(client, **kwargs):
    fn = load(patch.patched_messages(UPSTREAM), client)
    return asyncio.run(fn(chat_id=1, **kwargs))


def test_only_messages_after_since_come_back_newest_first():
    client = FakeClient(chat([5, 30, 59, 61, 120, 600]))
    since = (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert run(client, since=since, limit=300) == [1, 2, 3]


def test_reading_stops_at_the_first_older_message():
    client = FakeClient(chat([5, 61] + list(range(62, 400))))
    run(client, since=(NOW - timedelta(hours=1)).isoformat(), limit=300)
    assert client.read == 2


def test_a_busy_chat_stops_at_the_limit_with_its_newest():
    client = FakeClient(chat(range(0, 50)))
    out = run(client, since=(NOW - timedelta(hours=1)).isoformat(), limit=10)
    assert out == list(range(1, 11))


def test_nothing_after_since_says_so():
    client = FakeClient(chat([90, 120]))
    out = run(client, since=(NOW - timedelta(hours=1)).isoformat(), limit=300)
    assert out == "No messages found matching the criteria."


def test_a_message_exactly_at_since_counts():
    client = FakeClient(chat([60, 61]))
    assert run(client, since=(NOW - timedelta(hours=1)).isoformat(), limit=300) == [1]


def test_a_since_without_a_zone_is_utc():
    client = FakeClient(chat([5, 61]))
    naive = (NOW - timedelta(hours=1)).replace(tzinfo=None).isoformat()
    assert run(client, since=naive, limit=300) == [1]


def test_a_bad_since_is_refused():
    out = run(FakeClient([]), since="yesterday", limit=300)
    assert out.startswith("Invalid since format")


@pytest.mark.parametrize("other", [{"search_query": "x"}, {"from_date": "2026-10-05"}, {"to_date": "2026-10-05"}])
def test_since_does_not_mix_with_the_other_filters(other):
    out = run(FakeClient([]), since=NOW.isoformat(), limit=300, **other)
    assert out.startswith("since cannot be combined")


def test_without_since_upstream_behaviour_is_unchanged():
    class Plain:
        async def get_messages(self, entity, limit, **params):
            return chat(range(limit))

    assert run(Plain(), limit=3) == [1, 2, 3]


def test_the_tool_documents_since():
    out = patch.patched_messages(UPSTREAM)
    assert "since: str = None," in out
    assert "        since: " in out


def test_patching_twice_changes_nothing():
    once = patch.patched_messages(UPSTREAM)
    assert patch.patched_messages(once) == once


def test_a_file_without_the_anchors_is_refused():
    with pytest.raises(patch.PatchError):
        patch.patched_messages(UPSTREAM.replace("        params = {}\n", ""))


def test_a_file_with_two_list_messages_is_refused():
    with pytest.raises(patch.PatchError):
        patch.patched_messages(UPSTREAM + UPSTREAM)


def test_main_patches_the_file_in_place(tmp_path):
    messages = tmp_path / "messages.py"
    messages.write_text(UPSTREAM)
    assert patch.main(["prog", str(messages)]) == 0
    assert "since: str = None," in messages.read_text()
