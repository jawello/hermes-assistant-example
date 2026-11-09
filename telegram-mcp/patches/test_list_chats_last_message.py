"""Tests for the list_chats last_message_at patch.

The patch must land right after the `archived` field in a file shaped like
upstream's, refuse a file it does not recognise, and the lines it inserts
must turn a Telethon dialog's date into an ISO timestamp.
"""
import textwrap
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import list_chats_last_message as patch

# Shaped like telegram_mcp/tools/chats.py:list_chats at v3.2.65 (6072a24),
# around the record fields the patch reasons about.
UPSTREAM = '''\
            record["unread"] = unread_count
            if unread_mark:
                record["unread_mark"] = True
            record["muted"] = is_muted
            record["archived"] = bool(getattr(dialog, "archived", False))

            # Add unread mentions count if available
            unread_mentions = getattr(dialog, "unread_mentions_count", 0) or 0
            if unread_mentions > 0:
                record["unread_mentions"] = unread_mentions

            records.append(record)
'''


def test_the_field_lands_right_after_archived():
    out = patch.patched_chats(UPSTREAM)
    assert out.count('record["last_message_at"]') == 1
    assert out.index('record["archived"]') < out.index('record["last_message_at"]')
    assert out.index('record["last_message_at"]') < out.index("# Add unread mentions")


def test_patching_twice_changes_nothing():
    once = patch.patched_chats(UPSTREAM)
    assert patch.patched_chats(once) == once


def test_a_file_without_the_anchor_is_refused():
    with pytest.raises(patch.PatchError):
        patch.patched_chats('record["muted"] = is_muted\n')


def test_a_file_with_two_anchors_is_refused():
    with pytest.raises(patch.PatchError):
        patch.patched_chats(UPSTREAM + UPSTREAM)


def run_inserted(dialog):
    record = {}
    exec(textwrap.dedent(patch.INSERT), {"getattr": getattr}, {"record": record, "dialog": dialog})
    return record["last_message_at"]


def test_the_inserted_lines_give_the_dialog_date_in_iso():
    when = datetime(2026, 10, 5, 6, 30, tzinfo=timezone.utc)
    assert run_inserted(SimpleNamespace(date=when)) == "2026-10-05T06:30:00+00:00"


def test_a_dialog_without_a_date_gives_none():
    assert run_inserted(SimpleNamespace(date=None)) is None
    assert run_inserted(SimpleNamespace()) is None


def test_main_patches_the_file_in_place(tmp_path):
    chats = tmp_path / "chats.py"
    chats.write_text(UPSTREAM)
    assert patch.main(["prog", str(chats)]) == 0
    assert 'record["last_message_at"]' in chats.read_text()


def test_a_record_rebuilt_before_it_is_appended_is_refused():
    # If upstream ever whitelists the fields it returns, the patched field
    # would vanish quietly; the build must fail instead.
    rebuilt = UPSTREAM.replace(
        "            records.append(record)\n",
        "            record = {k: record[k] for k in KEEP}\n            records.append(record)\n",
    )
    with pytest.raises(patch.PatchError):
        patch.patched_chats(rebuilt)


def test_a_file_where_the_record_is_never_appended_is_refused():
    with pytest.raises(patch.PatchError):
        patch.patched_chats(UPSTREAM.replace("            records.append(record)\n", ""))
