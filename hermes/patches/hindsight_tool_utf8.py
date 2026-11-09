#!/usr/bin/env python3
"""Send hindsight tool results to the model as UTF-8, not \\u escapes.

**The bug.** `plugins/memory/hindsight/__init__.py:handle_tool_call` wraps
every result in `json.dumps({"result": ...})` with the default
`ensure_ascii=True`, so each Cyrillic letter reaches the model as a
six-character `\\uXXXX`. A recall of ~13K characters of Russian facts came
back as 56-68K characters and grew the next call's input by ~29K tokens
(62K -> 91K, 2026-10-09), which also pulls the 128K compression trigger
closer. The plugin's own retain path already passes `ensure_ascii=False`.

**The fix.** Pass `ensure_ascii=False` in the tool wrapper too. The JSON is
the same; only non-ASCII stays as itself.

The patch fails the build if the wrapper changes shape, so an unpatched
image cannot ship quietly.

Usage: `python hindsight_tool_utf8.py <hermes source dir>`
"""
import sys
from pathlib import Path


class PatchError(Exception):
    """The target file is not the one this patch was written against."""


UPSTREAM = '            return json.dumps({"result": handler(self, args)})\n'
PATCHED = (
    "            # UTF-8, not \\\\u escapes: Russian memories cost ~5x the tokens\n"
    "            # escaped (patched in by personal-assistant's hindsight_tool_utf8.py).\n"
    '            return json.dumps({"result": handler(self, args)}, ensure_ascii=False)\n'
)


def patched_plugin(source: str) -> str:
    """The hindsight plugin whose tool results keep non-ASCII text as is."""
    if PATCHED in source:
        return source
    found = source.count(UPSTREAM)
    if found != 1:
        raise PatchError(
            "expected exactly one tool-result json.dumps in "
            f"plugins/memory/hindsight/__init__.py, found {found} — upstream "
            "changed handle_tool_call; re-check this patch"
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
        print(f"{plugin}: tool results now keep UTF-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
