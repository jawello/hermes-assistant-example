#!/usr/bin/env python3
"""Let hermes hear "[ SILENT ]" as silence.

**The bug.** `gateway/response_filters.py:_canonical_silence_candidate`
collapses whitespace before comparing a response to the silence markers, so
`[SILENT]` and `SILENT` match but `[ SILENT ]` does not. On 2026-09-26 the
suggestions cron job answered `[ SILENT ]` and the user got those ten characters
in Telegram. Seen on 0.18.0 (then in `cron/scheduler.py:_is_token`); 0.21.5
moved the check into `response_filters.py` with the same gap.

**The fix.** Also drop the spaces just inside the brackets in the canonical
form. Only marker-sized responses are canonicalised, so a report that
mentions the marker mid-sentence is still delivered, as upstream intends.
Cron and live chat share this canonicaliser, so a bare `[ SILENT ]` chat
reply is swallowed too, exactly as `[SILENT]` already is.

The patch fails the build if the canonicaliser changes shape, so an unpatched
image cannot ship quietly.

Usage: `python cron_silence_tokens.py [site-packages dir]`
"""
import sys
import sysconfig
from pathlib import Path


class PatchError(Exception):
    """The target file is not the one this patch was written against."""


UPSTREAM_CANONICAL = (
    "def _canonical_silence_candidate(text: str) -> str:\n"
    '    return " ".join(text.strip().upper().split())\n'
)
PATCHED_CANONICAL = (
    "def _canonical_silence_candidate(text: str) -> str:\n"
    '    candidate = " ".join(text.strip().upper().split())\n'
    "    # \"[ SILENT ]\" is the same marker as \"[SILENT]\" (patched in by\n"
    "    # personal-assistant's cron_silence_tokens.py).\n"
    '    return candidate.replace("[ ", "[").replace(" ]", "]")\n'
)


def patched_filters(source: str) -> str:
    """`gateway/response_filters.py` whose canonical form ignores spaces inside brackets."""
    if PATCHED_CANONICAL in source:
        return source
    found = source.count(UPSTREAM_CANONICAL)
    if found != 1:
        raise PatchError(
            "expected exactly one _canonical_silence_candidate in "
            f"gateway/response_filters.py, found {found} — upstream changed its "
            "silence check; re-check this patch"
        )
    return source.replace(UPSTREAM_CANONICAL, PATCHED_CANONICAL, 1)


def main(argv: list[str]) -> int:
    # Resolved from the interpreter's layout rather than by importing the
    # package: this runs during `docker build`, where importing hermes would
    # pull in config and a HERMES_HOME that does not exist yet.
    root = Path(argv[1]) if len(argv) > 1 else Path(sysconfig.get_paths()["purelib"])
    filters = root / "gateway" / "response_filters.py"
    source = filters.read_text()
    patched = patched_filters(source)
    if patched == source:
        print(f"{filters}: already patched")
    else:
        filters.write_text(patched)
        print(f"{filters}: \"[ SILENT ]\" now counts as silence")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
