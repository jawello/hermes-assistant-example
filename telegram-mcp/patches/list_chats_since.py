#!/usr/bin/env python3
"""Let `list_chats` return only the chats that moved since an exact moment.

**Why.** The Telegram passes run every hour and read only the chats with
something new since the last finished pass. Upstream's `list_chats`
(chigwell/telegram-mcp v3.2.65) has no date filter, so every pass got all 200
dialogs — about 73K characters — to keep the handful that moved, and the
model sometimes asked again with other parameters for the same answer.

`since` takes an ISO 8601 moment (UTC unless it says otherwise) and drops a
dialog whose newest message is older than it, after the upstream filters.
`limit` still counts dialogs fetched from Telegram before any filter. A
dialog without a date is kept: one chat too many is better than one dropped
quietly.

Apply after list_chats_last_message.py. The same change is offered upstream;
drop this patch once a pinned release carries it. The patch fails the build
if an anchor is gone or ambiguous, so an image without the parameter cannot
ship quietly.

Usage: `python list_chats_since.py <telegram_mcp/tools/chats.py>`
"""
import sys
from pathlib import Path


class PatchError(Exception):
    """The target file is not the one this patch was written against."""


MARK = "(patched in by personal-assistant's list_chats_since.py)"

# (anchor, replacement) pairs, each anchor exactly once in the file.
EDITS = [
    (
        '    with_about: bool = False,\n'
        '    account: str = None,\n'
        ') -> str:\n',
        '    with_about: bool = False,\n'
        '    since: str = None,\n'
        '    account: str = None,\n'
        ') -> str:\n',
    ),
    (
        "        with_about: If True, fetch each chat's description/bio via an additional\n",
        '        since: Only chats whose newest message is at or after this moment\n'
        '            (ISO 8601, e.g. 2026-10-05T18:30:00Z; UTC if no zone).\n'
        "        with_about: If True, fetch each chat's description/bio via an additional\n",
    ),
    (
        '        try:\n'
        '            dialogs = await cl.get_dialogs(limit=limit, archived=archived)\n',
        f'        # since: an exact moment {MARK}.\n'
        '        since_obj = None\n'
        '        if since:\n'
        '            try:\n'
        '                since_obj = datetime.fromisoformat(since.replace("Z", "+00:00"))\n'
        '            except ValueError:\n'
        '                return "Invalid since format. Use ISO 8601, e.g. 2026-10-05T18:30:00Z."\n'
        '            if since_obj.tzinfo is None:\n'
        '                from datetime import timezone as _utc\n'
        '\n'
        '                since_obj = since_obj.replace(tzinfo=_utc.utc)\n'
        '\n'
        '        try:\n'
        '            dialogs = await cl.get_dialogs(limit=limit, archived=archived)\n',
    ),
    (
        '            if archived is not None and bool(getattr(dialog, "archived", False)) != archived:\n'
        '                continue\n',
        '            if archived is not None and bool(getattr(dialog, "archived", False)) != archived:\n'
        '                continue\n'
        '\n'
        '            # Quiet since `since`; a dialog without a date is kept.\n'
        '            dialog_date = getattr(dialog, "date", None)\n'
        '            if since_obj is not None and dialog_date is not None and dialog_date < since_obj:\n'
        '                continue\n',
    ),
]


def patched_chats(source: str) -> str:
    """`telegram_mcp/tools/chats.py` whose list_chats takes `since`."""
    if MARK in source:
        missing = [new for _, new in EDITS if new not in source]
        if missing:
            raise PatchError("list_chats is only partly patched; re-check this patch")
        return source
    for anchor, replacement in EDITS:
        found = source.count(anchor)
        if found != 1:
            raise PatchError(
                f"expected one anchor in list_chats, found {found}: "
                f"{anchor.splitlines()[0].strip()!r} — upstream changed "
                "list_chats; re-check this patch"
            )
        source = source.replace(anchor, replacement, 1)
    return source


def main(argv: list[str]) -> int:
    chats = Path(argv[1])
    source = chats.read_text()
    patched = patched_chats(source)
    if patched == source:
        print(f"{chats}: already patched")
    else:
        chats.write_text(patched)
        print(f"{chats}: list_chats now takes since")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
