#!/usr/bin/env python3
"""Hide MCP tools behind tool search only when there are enough of them.

**Why.** With `tools.tool_search.enabled` on, hermes 0.21.5 replaces every MCP
tool with the tool_search / tool_describe / tool_call bridge as soon as one
exists; "auto" is an alias of "on" and no size threshold is left. That pays
off in chat (~190 tools, ~42K tokens). The cron jobs are pinned to one MCP
server (13 tools, 1.5–3K tokens), and there the model spent three of nine API
calls of a Telegram pass finding its tools, then one more on a
`tool_call takes exactly one entry` error from batching through the bridge
(2026-10-05).

**The fix.** `min_deferred_tokens` (default 0 = upstream): a surface lighter
than that counts as no surface, so its tools stay direct. Connectors in scope
still turn the bridge on, as upstream. Every decision is logged, the eager one
too, so a surface that outgrows the floor shows in agent.log.

The patch fails the build if an anchor is gone or ambiguous, so an image
without the floor cannot ship quietly.
Design: design notes not published

Usage: `python tool_search_min_tokens.py <hermes source dir>`
"""
import sys
from pathlib import Path


class PatchError(Exception):
    """The target file is not the one this patch was written against."""


MARK = "(patched in by personal-assistant's tool_search_min_tokens.py)"

# (anchor, replacement) pairs, each anchor exactly once in the file.
EDITS = [
    (
        "    defer_tools: Optional[frozenset] = None\n",
        "    defer_tools: Optional[frozenset] = None\n"
        "    # Deferrable tools lighter than this stay direct: no bridge\n"
        f"    # {MARK}.\n"
        "    min_deferred_tokens: int = 0\n",
    ),
    (
        '            listing_max_tokens=_clamped_int(raw.get("listing_max_tokens"), 4000, 200, 60000),\n',
        '            listing_max_tokens=_clamped_int(raw.get("listing_max_tokens"), 4000, 200, 60000),\n'
        "            min_deferred_tokens=_clamped_int(\n"
        '                raw.get("min_deferred_tokens"), 0, 0, 1_000_000),\n',
    ),
    (
        "    if deferrable_tokens > 0:\n"
        "        return True\n"
        "    return connections_granted\n",
        "    # Below min_deferred_tokens a surface counts as none: its tools stay direct.\n"
        "    if deferrable_tokens > 0 and deferrable_tokens >= config.min_deferred_tokens:\n"
        "        return True\n"
        "    return connections_granted\n",
    ),
    (
        "    if not should_activate(config, deferrable_tokens, context_length):\n"
        "        return AssemblyResult(\n",
        "    if not should_activate(config, deferrable_tokens, context_length,\n"
        "                           connections_granted=connections_granted):\n"
        '        if config.enabled != "off":\n'
        "            logger.info(\n"
        '                "tool_search kept eager: %d deferrable tools (~%d tokens) below "\n'
        '                "min_deferred_tokens %d", len(deferrable), deferrable_tokens,\n'
        "                config.min_deferred_tokens)\n"
        "        return AssemblyResult(\n",
    ),
]


def patched_tool_search(source: str) -> str:
    """`tools/tool_search.py` whose bridge waits for min_deferred_tokens."""
    if MARK in source:
        missing = [new for _, new in EDITS if new not in source]
        if missing:
            raise PatchError("tool_search.py is only partly patched; re-check this patch")
        return source
    for anchor, replacement in EDITS:
        found = source.count(anchor)
        if found != 1:
            raise PatchError(
                f"expected one anchor in tools/tool_search.py, found {found}: "
                f"{anchor.splitlines()[0].strip()!r} — upstream changed tool "
                "search; re-check this patch"
            )
        source = source.replace(anchor, replacement, 1)
    return source


def main(argv: list[str]) -> int:
    target = Path(argv[1]) / "tools" / "tool_search.py"
    source = target.read_text()
    patched = patched_tool_search(source)
    if patched == source:
        print(f"{target}: already patched")
    else:
        target.write_text(patched)
        print(f"{target}: tool search now waits for min_deferred_tokens")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
