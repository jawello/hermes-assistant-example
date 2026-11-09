#!/usr/bin/env python3
"""Make background recall search by the user's own words, not the gateway's notes.

**The bug.** The gateway puts notes before the user's text: `[Replying to: "<quote>"]`, a
document note, an image description. That whole text became the recall query, cut at 800
characters. In 7 of 48 replies over 14 days the user's words were cut off entirely. With an
unrelated quote first, the 30 recall questions were found 17 times instead of 29.

**The fix.** `_do_recall` searches by the user's own words. Under 40 characters they cannot
carry a topic ("да, сделай", no caption), so the topic of the notes follows them and the query
stops at 150 characters: rerank time grows with length, and a long tail drowns the words.
A note with neither words nor a topic searches nothing. A message with no gateway note,
including one the user started with `[`, is searched as before. So is the `hindsight_recall`
tool, which has its own path and the query the model wrote.

**Cron passes.** Every job's prompt opens with the same 1124-character hint from the
scheduler, so with `recall_sync` on all jobs would search by it and find the same memories.
The hint is removed the same way, and a pass searches by the job's own prompt.

The patch fails the build if `_do_recall` changes shape or hermes rewords a note, so the
notes cannot quietly return to the query.

Design: design notes not published and
design notes not published

Usage: `python hindsight_query_own_text.py <hermes source dir>`
"""
import sys
from pathlib import Path


class PatchError(Exception):
    """The target files are not the ones this patch was written against."""


UPSTREAM_CLASS = "class HindsightMemoryProvider(MemoryProvider):\n"
HELPER = r'''# Notes hermes puts before the user's words or a job's prompt: how each opens, what ends its
# topic (None if it has none) and what ends the note (patched in by personal-assistant's
# hindsight_query_own_text.py).
_GATEWAY_NOTES = (
    ("[IMPORTANT: You are running as a scheduled cron job. ", None, "]\n\n"),
    ('[Replying to your previous message: "', '"]', '"]\n\n'),
    ('[Replying to: "', '"]', '"]\n\n'),
    ("[The user sent an image~ Here's what I can see:\n", "]\n[If you need", "~]\n\n"),
    ("[The user sent a text document: '", "'. ", "]\n\n"),
    ("[The user sent a document: '", "'. ", "]\n\n"),
    ("[The user sent an audio file attachment: '", "'. ", "]\n\n"),
    ("[The user sent a video attachment: '", "'. ", "]\n\n"),
    ("[The user sent ", None, "]\n\n"),
)
_RECALL_ENOUGH_OWN_WORDS = 40
_RECALL_SHORT_QUERY_CAP = 150


def _recall_query(message: str) -> str:
    """What to search by: the user's words, plus the notes' topic when the words are few."""
    own, topics = message, []
    while note := next((n for n in _GATEWAY_NOTES if own.startswith(n[0])), None):
        opening, topic_end, note_end = note
        body, _, own = own[len(opening) :].partition(note_end)
        if topic_end:
            topics.append(body.partition(topic_end)[0].strip())
    own = own.strip()
    if len(own) >= _RECALL_ENOUGH_OWN_WORDS:
        return own
    return "\n\n".join(part for part in (own, *topics) if part)[:_RECALL_SHORT_QUERY_CAP]


'''

UPSTREAM_RECALL = (
    '        -> (text, memory count); the count is 0 for reflect (synthesis) and on error."""\n'
    "        if self._recall_max_input_chars:\n"
)
PATCHED_RECALL = (
    '        -> (text, memory count); the count is 0 for reflect (synthesis) and on error."""\n'
    "        query = _recall_query(query)\n"
    "        if not query:\n"
    '            logger.debug("Recall: skipped (a gateway note with no words and no topic)")\n'
    '            return "", 0\n'
    "        if self._recall_max_input_chars:\n"
)

# What HELPER relies on, as it reads in hermes' source.
GATEWAY_WORDING = {
    "cron/scheduler_prompt.py": (
        r'"[IMPORTANT: You are running as a scheduled cron job. "',
        r'another job.]\n\n"',
    ),
    "gateway/run_inbound.py": (
        r'_who = " your previous message"',
        r'[Replying to{_who}: "{reply_text}"]\n\n{message_text}',
        r'("an audio file attachment",',
        r'("a video attachment",',
        r"[The user sent {kind}: '{_display}'. ",
        r"[The user sent an image~ Here's what I can see:\n{description}]\n",
        r"[If you need a closer look, ",
        r"image_url: {path} ~]",
    ),
    "gateway/run.py": (
        r"[The user sent a text document: '{display_name}'. ",
        r"[The user sent a document: '{display_name}'. ",
    ),
}


def patched_plugin(source: str) -> str:
    """The hindsight plugin whose background recall searches by the user's own words."""
    if HELPER in source and PATCHED_RECALL in source:
        return source
    for name, upstream in (("_do_recall", UPSTREAM_RECALL), ("the provider class", UPSTREAM_CLASS)):
        found = source.count(upstream)
        if found != 1:
            raise PatchError(
                f"expected exactly one match for {name} in "
                f"plugins/memory/hindsight/__init__.py, found {found} — upstream "
                "changed the plugin; re-check this patch"
            )
    return source.replace(UPSTREAM_RECALL, PATCHED_RECALL, 1).replace(
        UPSTREAM_CLASS, HELPER + UPSTREAM_CLASS, 1
    )


def check_gateway_wording(hermes_src: Path) -> None:
    """Refuse a hermes whose notes no longer read the way HELPER expects."""
    for relative, needles in GATEWAY_WORDING.items():
        source = (hermes_src / relative).read_text()
        for needle in needles:
            if needle not in source:
                raise PatchError(
                    f"{relative} no longer contains {needle!r} — hermes reworded a note; "
                    "update _GATEWAY_NOTES in this patch"
                )


def main(argv: list[str]) -> int:
    hermes_src = Path(argv[1])
    check_gateway_wording(hermes_src)
    plugin = hermes_src / "plugins" / "memory" / "hindsight" / "__init__.py"
    source = plugin.read_text()
    patched = patched_plugin(source)
    if patched == source:
        print(f"{plugin}: already patched")
    else:
        plugin.write_text(patched)
        print(f"{plugin}: background recall now searches by the user's own words")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
