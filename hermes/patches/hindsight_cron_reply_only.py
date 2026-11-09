#!/usr/bin/env python3
"""Keep the job's prompt out of Hindsight when a cron turn is retained.

**The bug.** `cron/scheduler.py` runs jobs with `skip_memory=False`, so the
hindsight plugin retains each pass with the job's prompt, and fact extraction
retells that prompt. On 2026-11-02, 648 of 1010 cron units were such retellings,
and they made up 58% of recall results across 12 questions.

**The fix.** In `sync_turn`, for the `cron` platform, keep only the assistant
message and skip a reply that is empty or the silence marker. The facts in a
real reply still reach memory, and chat turns are not touched.

The patch fails the build if `sync_turn` changes shape, so an unpatched image
cannot ship quietly.

Design: design notes not published

Usage: `python hindsight_cron_reply_only.py <hermes source dir>`
"""
import sys
from pathlib import Path


class PatchError(Exception):
    """The target file is not the one this patch was written against."""


UPSTREAM = (
    "        self._session_turns.append(\n"
    "            json.dumps(self._build_turn_messages(user_content, assistant_content), ensure_ascii=False)\n"
    "        )\n"
)
PATCHED = (
    "        turn = self._build_turn_messages(user_content, assistant_content)\n"
    '        if self._platform == "cron":\n'
    "            # The reply only: extraction retold the job's prompt as facts, and a\n"
    "            # silent pass has nothing to keep (patched in by personal-assistant's\n"
    "            # hindsight_cron_reply_only.py).\n"
    "            from gateway.response_filters import is_autonomous_silence_response\n"
    "\n"
    "            if not assistant_content.strip() or is_autonomous_silence_response(assistant_content):\n"
    '                logger.debug("sync_turn: skipped silent cron turn")\n'
    "                return\n"
    '            turn = [message for message in turn if message["role"] == "assistant"]\n'
    "        self._session_turns.append(json.dumps(turn, ensure_ascii=False))\n"
)


def patched_plugin(source: str) -> str:
    """The hindsight plugin whose cron turns retain only a real reply."""
    if PATCHED in source:
        return source
    found = source.count(UPSTREAM)
    if found != 1:
        raise PatchError(
            "expected exactly one turn append in sync_turn in "
            f"plugins/memory/hindsight/__init__.py, found {found} — upstream "
            "changed sync_turn; re-check this patch"
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
        print(f"{plugin}: cron turns now retain only the reply")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
