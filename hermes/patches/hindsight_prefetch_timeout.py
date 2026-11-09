#!/usr/bin/env python3
"""Give an external memory provider's prefetch 15 s instead of 8 s.

**The problem.** `agent/memory_manager.py` waits `_EXTERNAL_PREFETCH_TIMEOUT_S`
(8 s) for a provider's prefetch, then the turn goes on with no memory. With
`recall_sync` on, the Hindsight search runs inside that wait. On 2026-11-05 the
08:30 `telegram-digest` pass missed it by about 0.2 s: the Hindsight log said
6.7 s, and hermes started counting about 1.5 s before Hindsight did. Cron searches
take 4.9-6.7 s in the Hindsight log, and Hindsight reranks one query at a time,
so two searches at once wait for each other.

**The fix.** 15 s. A search that would have made it changes nothing, since the
wait ends as soon as the result arrives. Only a slow search waits longer, and
it now brings its memory. Hermes has no config key for this; an explicit
`external_prefetch_timeout` argument still wins.

The patch fails the build if the default or its use changes shape, so an
unpatched image cannot ship quietly.

Usage: `python hindsight_prefetch_timeout.py <hermes source dir>`
"""
import sys
from pathlib import Path


class PatchError(Exception):
    """The target file is not the one this patch was written against."""


UPSTREAM = "_EXTERNAL_PREFETCH_TIMEOUT_S = 8.0\n"
PATCHED = (
    "# 15 s, not 8: a cron search missed 8 s by 0.2 s on 2026-11-05\n"
    "# (patched in by personal-assistant's hindsight_prefetch_timeout.py).\n"
    "_EXTERNAL_PREFETCH_TIMEOUT_S = 15.0\n"
)
USE = "timeout = _EXTERNAL_PREFETCH_TIMEOUT_S if timeout is None else float(timeout)\n"


def patched_manager(source: str) -> str:
    """The memory manager whose external prefetch waits 15 s."""
    if source.count(USE) != 1:
        raise PatchError(
            "expected the default timeout to be used once in agent/memory_manager.py "
            "— upstream changed MemoryManager.__init__; re-check this patch"
        )
    if PATCHED in source:
        return source
    found = source.count(UPSTREAM)
    if found != 1:
        raise PatchError(
            f"expected exactly one 8 s default in agent/memory_manager.py, found {found} "
            "— upstream changed the prefetch timeout; re-check this patch"
        )
    return source.replace(UPSTREAM, PATCHED, 1)


def main(argv: list[str]) -> int:
    manager = Path(argv[1]) / "agent" / "memory_manager.py"
    source = manager.read_text()
    patched = patched_manager(source)
    if patched == source:
        print(f"{manager}: already patched")
    else:
        manager.write_text(patched)
        print(f"{manager}: external prefetch now waits 15 s")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
