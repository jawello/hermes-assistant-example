#!/usr/bin/env python3
"""Stop cron passes from queueing a recall nobody can read.

**The bug.** After every turn hermes calls `queue_prefetch`, which warms recall
for the next turn. A cron pass has one turn, and then its agent closes and the
request is dropped: 15 of 15 passes ended `[RECALL CANCELLED] client disconnected`
after 9-14 s on 2026-11-03/04. The query is the first 800 characters of the job's
prompt, so the reranker works hard for nothing.

**The fix.** `queue_prefetch` returns at once on the `cron` platform. A pass gets
exactly what it got before: `prefetch` on its only turn had nothing queued, and
the `hindsight_recall` tool is untouched. Every other platform stays as it is.

The patch fails the build if `queue_prefetch` changes shape, so an unpatched image
cannot ship quietly.

Design: design notes not published

Usage: `python hindsight_cron_no_prefetch.py <hermes source dir>`
"""
import sys
from pathlib import Path


class PatchError(Exception):
    """The target file is not the one this patch was written against."""


UPSTREAM = (
    "        if self._recall_sync or self._recall_disabled():\n"
    "            return\n"
)
PATCHED = UPSTREAM + (
    '        if self._platform == "cron":\n'
    "            # One turn, then the agent closes: nothing can read this recall\n"
    "            # (patched in by personal-assistant's hindsight_cron_no_prefetch.py).\n"
    '            logger.debug("Prefetch: skipped (a cron pass has no next turn)")\n'
    "            return\n"
)


def patched_plugin(source: str) -> str:
    """The hindsight plugin whose cron passes queue no recall."""
    if PATCHED in source:
        return source
    found = source.count(UPSTREAM)
    if found != 1:
        raise PatchError(
            "expected exactly one guard in queue_prefetch in "
            f"plugins/memory/hindsight/__init__.py, found {found} — upstream "
            "changed queue_prefetch; re-check this patch"
        )
    return source.replace(UPSTREAM, PATCHED, 1)


def main(argv: list[str]) -> int:
    plugin = Path(argv[1]) / "plugins" / "memory" / "hindsight" / "__init__.py"
    source = plugin.read_text()
    patched = patched_plugin(source)
    if patched == source:
        print(f"{plugin}: already patched")
    else:
        plugin.write_text(patched)
        print(f"{plugin}: cron passes now queue no recall")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
