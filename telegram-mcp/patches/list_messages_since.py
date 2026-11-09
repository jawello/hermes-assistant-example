#!/usr/bin/env python3
"""Let `list_messages` return only what came after an exact moment.

**Why.** The Telegram passes run every hour and read only what arrived since
the last finished pass. Upstream's `list_messages` (chigwell/telegram-mcp
v3.2.65) filters by a whole date, and with `from_date` it walks oldest ->
newest, so a busy chat hits its limit before it reaches the newest messages —
the ones that need an answer. Without a filter the pass had to take the
newest 300 messages of every chat and drop the old ones itself: 70–160K
characters per chat to find the two new messages.

`since` takes an ISO 8601 moment (UTC unless it says otherwise), walks
newest -> oldest and stops at the first message older than it, so neither
Telegram nor the tool answer carries what an earlier pass already read. A
chat that returns exactly `limit` messages may have had more.

The same change is offered upstream; drop this patch once a pinned release
carries it. The patch fails the build if an anchor is gone or ambiguous, so
an image without the parameter cannot ship quietly.

Usage: `python list_messages_since.py <telegram_mcp/tools/messages.py>`
"""
import sys
from pathlib import Path


class PatchError(Exception):
    """The target file is not the one this patch was written against."""


MARK = "(patched in by personal-assistant's list_messages_since.py)"

# (anchor, replacement) pairs, each anchor exactly once in the file.
EDITS = [
    (
        '    to_date: str = None,\n'
        '    account: str = None,\n'
        ') -> str:\n'
        '    """\n'
        '    Retrieve messages with optional filters.\n',
        '    to_date: str = None,\n'
        '    since: str = None,\n'
        '    account: str = None,\n'
        ') -> str:\n'
        '    """\n'
        '    Retrieve messages with optional filters.\n',
    ),
    (
        '        to_date: Filter messages until this date (format: YYYY-MM-DD).\n',
        '        to_date: Filter messages until this date (format: YYYY-MM-DD).\n'
        '        since: Only messages at or after this moment (ISO 8601, e.g.\n'
        '            2026-10-05T18:30:00Z; UTC if no zone), newest first. Cannot be\n'
        '            combined with search_query, from_date or to_date.\n',
    ),
    (
        '        # Prepare filter parameters\n'
        '        params = {}\n'
        '        if search_query:\n',
        f'        # since: an exact moment, read newest -> oldest {MARK}.\n'
        '        since_obj = None\n'
        '        if since:\n'
        '            if search_query or from_date or to_date:\n'
        '                return "since cannot be combined with search_query, from_date or to_date."\n'
        '            try:\n'
        '                since_obj = datetime.fromisoformat(since.replace("Z", "+00:00"))\n'
        '            except ValueError:\n'
        '                return "Invalid since format. Use ISO 8601, e.g. 2026-10-05T18:30:00Z."\n'
        '            if since_obj.tzinfo is None:\n'
        '                from datetime import timezone as _utc\n'
        '\n'
        '                since_obj = since_obj.replace(tzinfo=_utc.utc)\n'
        '\n'
        '        # Prepare filter parameters\n'
        '        params = {}\n'
        '        if since_obj is not None:\n'
        '            messages = []\n'
        '            async for msg in cl.iter_messages(entity):  # newest -> oldest\n'
        '                if msg.date < since_obj:\n'
        '                    break\n'
        '                messages.append(msg)\n'
        '                if len(messages) >= limit:\n'
        '                    break\n'
        '        elif search_query:\n',
    ),
]


def patched_messages(source: str) -> str:
    """`telegram_mcp/tools/messages.py` whose list_messages takes `since`."""
    if MARK in source:
        missing = [new for _, new in EDITS if new not in source]
        if missing:
            raise PatchError("list_messages is only partly patched; re-check this patch")
        return source
    for anchor, replacement in EDITS:
        found = source.count(anchor)
        if found != 1:
            raise PatchError(
                f"expected one anchor in list_messages, found {found}: "
                f"{anchor.splitlines()[0].strip()!r} — upstream changed "
                "list_messages; re-check this patch"
            )
        source = source.replace(anchor, replacement, 1)
    return source


def main(argv: list[str]) -> int:
    messages = Path(argv[1])
    source = messages.read_text()
    patched = patched_messages(source)
    if patched == source:
        print(f"{messages}: already patched")
    else:
        messages.write_text(patched)
        print(f"{messages}: list_messages now takes since")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
