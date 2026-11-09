#!/usr/bin/env python3
"""Register our own tool modules with upstream's server.

**Why.** Two modules are ours, copied into the image under
`telegram_mcp/tools/`:
- `send_gate/`: the confirmed send (send_message_confirmed, allow_chat,
  disallow_chat, list_allowed_chats);
- `history_search/`: search_history, over the whole history.
Upstream registers a tool module by importing it in
`telegram_mcp/tools/__init__.py`; this patch adds those imports after the
last upstream module, before `__all__`. Upstream code is not touched
otherwise.

The patch fails the build if the anchors are gone or ambiguous, so an image
whose own tools are silently missing cannot ship.
Design: design notes not published,
design notes not published

Usage: `python register_modules.py <telegram_mcp/tools/__init__.py>`
"""
import sys
from pathlib import Path


class PatchError(Exception):
    """The target file is not the one this patch was written against."""


ANCHOR = "from telegram_mcp.tools.events import *\n"
IMPORT = (
    "# Our own modules (patched in by personal-assistant's register_modules.py):\n"
    "# the confirmed send and the history search.\n"
    "from telegram_mcp.tools.send_gate.tools import *\n"
    "from telegram_mcp.tools.history_search.tools import *\n"
)


def patched_init(source: str) -> str:
    """`tools/__init__.py` that also imports the send gate."""
    if IMPORT in source:
        return source
    for anchor in (ANCHOR, "\n__all__"):
        found = source.count(anchor)
        if found != 1:
            raise PatchError(
                f"expected one {anchor.strip()!r} in tools/__init__.py, found {found} — "
                "upstream changed how tools are registered; re-check this patch"
            )
    if source.index(ANCHOR) > source.index("\n__all__"):
        raise PatchError("__all__ comes before the last tool import; re-check this patch")
    return source.replace(ANCHOR, ANCHOR + IMPORT, 1)


def main(argv: list[str]) -> int:
    target = Path(argv[1])
    source = target.read_text()
    patched = patched_init(source)
    if patched == source:
        print(f"{target}: already patched")
    else:
        target.write_text(patched)
        print(f"{target}: send gate and history search registered")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
