#!/usr/bin/env python3
"""Give every `list_chats` record the time its chat last moved.

**Why.** The morning Telegram pass reads only the chats with something new
since the last pass. Upstream's `list_chats` (chigwell/telegram-mcp v3.2.65)
reports unread counts but no date, and unread is the wrong signal: the user
reads most chats on his phone before 08:30, and a read message can still be
one he never answered. Without a date the pass would have to open every
chat. Telethon's `Dialog.date` is the date of the chat's newest message.

The same change is offered upstream; drop this patch once a pinned release
carries it. The patch fails the build if the anchor is gone or ambiguous, so
an image without the field cannot ship quietly.

Usage: `python list_chats_last_message.py <telegram_mcp/tools/chats.py>`
"""
import sys
from pathlib import Path


class PatchError(Exception):
    """The target file is not the one this patch was written against."""


ANCHOR = '            record["archived"] = bool(getattr(dialog, "archived", False))\n'
INSERT = (
    "            # When the chat last moved, so a daily pass can skip quiet chats\n"
    "            # (patched in by personal-assistant's list_chats_last_message.py).\n"
    '            record["last_message_at"] = (\n'
    '                dialog.date.isoformat() if getattr(dialog, "date", None) else None\n'
    "            )\n"
)


APPEND = "            records.append(record)\n"


def _check_record_reaches_the_result(source: str) -> None:
    """The record the field is written into must be the one appended, unchanged.

    If upstream ever rebuilds or whitelists the record before appending it,
    the patched field would vanish without an error; fail the build instead.
    """
    start = source.index(ANCHOR)
    end = source.find(APPEND, start)
    if end == -1:
        raise PatchError(
            "list_chats no longer appends `record` after the archived field — "
            "upstream changed list_chats; re-check this patch"
        )
    between = source[start + len(ANCHOR):end]
    if "            record = " in between:
        raise PatchError(
            "list_chats rebuilds `record` before appending it — the patched "
            "field would be dropped; re-check this patch"
        )


def patched_chats(source: str) -> str:
    """`telegram_mcp/tools/chats.py` whose list_chats records carry last_message_at."""
    if ANCHOR + INSERT in source:
        _check_record_reaches_the_result(source)
        return source
    found = source.count(ANCHOR)
    if found != 1:
        raise PatchError(
            f"expected exactly one archived field in list_chats, found {found} — "
            "upstream changed list_chats; re-check this patch"
        )
    _check_record_reaches_the_result(source)
    return source.replace(ANCHOR, ANCHOR + INSERT, 1)


def main(argv: list[str]) -> int:
    chats = Path(argv[1])
    source = chats.read_text()
    patched = patched_chats(source)
    if patched == source:
        print(f"{chats}: already patched")
    else:
        chats.write_text(patched)
        print(f"{chats}: list_chats now reports last_message_at")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
