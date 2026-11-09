"""Tests for the cron silence-token patch.

The patch must rewrite a file shaped like upstream's, refuse one it does not
recognise, and make hermes treat "[ SILENT ]" as silence while a real
report that merely mentions the word still gets delivered.
"""
import pytest

import cron_silence_tokens as patch

# Shaped like gateway/response_filters.py in hermes-agent 0.21.5: the
# canonicaliser and both matchers that share it (cron/webhook and live chat).
RESPONSE_FILTERS = '''\
import unicodedata
from typing import Any

LIVE_GATEWAY_SILENT_MARKERS = frozenset({
    "[SILENT]", "SILENT", "NO_REPLY", "NO REPLY",
})
_BRACKETED_SILENCE_MARKERS = tuple(
    sorted(m for m in LIVE_GATEWAY_SILENT_MARKERS if m.startswith("["))
)
_MARKER_LENGTH_CAP = 64


def _canonical_silence_candidate(text: str) -> str:
    return " ".join(text.strip().upper().split())


def _is_edge_punctuation(ch: str) -> bool:
    return ch not in "[]" and unicodedata.category(ch).startswith("P")


def _strip_edge_silence_punctuation(text: str) -> str:
    start, end = 0, len(text)
    while start < end and _is_edge_punctuation(text[start]):
        start += 1
    while end > start and _is_edge_punctuation(text[end - 1]):
        end -= 1
    return text[start:end].strip()


def _canonical_silence_candidates(text: Any) -> tuple[str, ...]:
    stripped = text.strip() if isinstance(text, str) else ""
    if not 0 < len(stripped) <= _MARKER_LENGTH_CAP:
        return ()
    depunctuated = _strip_edge_silence_punctuation(stripped)
    forms = (stripped,) if depunctuated == stripped else (stripped, depunctuated)
    return tuple(_canonical_silence_candidate(f) for f in forms)


def is_intentional_silence_response(response: Any) -> bool:
    return any(c in LIVE_GATEWAY_SILENT_MARKERS for c in _canonical_silence_candidates(response))


def is_autonomous_silence_response(response: Any) -> bool:
    stripped = response.strip() if isinstance(response, str) else ""
    if not stripped:
        return False
    lines = [ln for ln in stripped.splitlines() if ln.strip()]
    return stripped.upper().startswith(_BRACKETED_SILENCE_MARKERS) or any(
        is_intentional_silence_response(c) for c in (stripped, lines[0], lines[-1])
    )
'''


def silence_check(source: str, matcher: str = "is_autonomous_silence_response"):
    namespace = {}
    exec(compile(source, "response_filters.py", "exec"), namespace)
    return namespace[matcher]


def test_unpatched_filters_delivers_spaced_marker():
    # The bug this patch exists for: 2026-09-26 the model answered
    # "[ SILENT ]" and the user got it in Telegram.
    assert silence_check(RESPONSE_FILTERS)("[ SILENT ]") is False


@pytest.mark.parametrize("text", ["[ SILENT ]", "[SILENT ]", "[ silent]", "[  SILENT  ]",
                                  "note\n\n[ SILENT ]", "[SILENT]", "SILENT"])
def test_patched_filters_treats_spaced_marker_as_silence(text):
    assert silence_check(patch.patched_filters(RESPONSE_FILTERS))(text) is True


@pytest.mark.parametrize("text", ["Предложения на сегодня:\n1. Сделать [ SILENT ] вещь",
                                  "I considered staying [ SILENT ] but here is the summary"])
def test_patched_filters_still_delivers_reports(text):
    assert silence_check(patch.patched_filters(RESPONSE_FILTERS))(text) is False


def test_patched_chat_lane_also_treats_spaced_marker_as_silence():
    # Chat and cron share the canonicaliser, so a bare "[ SILENT ]" reply
    # in a conversation is swallowed too, as "[SILENT]" already is.
    check = silence_check(patch.patched_filters(RESPONSE_FILTERS), "is_intentional_silence_response")
    assert check("[ SILENT ]") is True


def test_patch_is_idempotent():
    once = patch.patched_filters(RESPONSE_FILTERS)
    assert patch.patched_filters(once) == once


def test_patch_refuses_an_unknown_scheduler():
    with pytest.raises(patch.PatchError):
        patch.patched_filters(RESPONSE_FILTERS.replace("def _canonical_silence_candidate(", "def _canon("))


def test_main_patches_the_file_in_place(tmp_path):
    filters = tmp_path / "gateway" / "response_filters.py"
    filters.parent.mkdir()
    filters.write_text(RESPONSE_FILTERS)
    assert patch.main(["cron_silence_tokens.py", str(tmp_path)]) == 0
    assert silence_check(filters.read_text())("[ SILENT ]") is True
