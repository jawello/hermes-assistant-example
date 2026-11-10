"""Speak a voice turn's short answer on the Station, then hand the rest to Telegram.

Installed into hermes as ``gateway/platforms/alice_speech.py`` and called from
the webhook platform's delivery by ``webhook_speak.py`` (both personal-assistant).
Design: design notes not published.

The ``alice`` route opts in with ``deliver_extra: {speak_on: "{speaker}"}``:
alice-bridge puts the Station's Quasar device id in the payload's ``speaker``
(empty when the command did not come from a known Station). The agent ends its
answer with a ``🔊 <phrase>`` line. Here that line is taken out, sent to
alice-bridge's ``/speak`` (signed like the forward leg, with
``ALICE_WEBHOOK_SECRET``), and the rest goes on to Telegram, with one 🔇 line
if the Station did not speak.

It must never raise and never delay Telegram by more than the 5 s timeout:
speech is a convenience, the Telegram answer is the record. Stdlib only at
import time; aiohttp is imported when the default poster first runs.
"""
import hashlib
import hmac
import json
import logging
import os
import re
import time

logger = logging.getLogger(__name__)

SPOKEN_PREFIX = "🔊"
MIC_PREFIX = "🎙"
MAX_SPOKEN = 100
TIMEOUT_S = 5
FALLBACK = "Ответ в Телеграме."
MUTE = "🔇 Станция не озвучила ответ"
MUTE_AUTH = "🔇 Станция не озвучила ответ: нужен повторный вход в Яндекс"
DEFAULT_URL = "http://alice-bridge:8000/speak"

# Markup or quotes the model may wrap the line in: **🔊 …**, - 🔊 …, > 🔊 …, «🔊 …».
_SPOKEN_LINE = re.compile(r"^[\s>*_~`«\"'\-]*" + SPOKEN_PREFIX + "\ufe0f?(.*)$")
# hermes' memory recall indicator (agent/memory_manager.py describe_recall),
# e.g. "👁️ Hindsight — recalled 22 memories": noise in a voice turn's Telegram.
_RECALL_NOTICE = re.compile(r"^\S+\s+[^\n]+ — recalled (?:\d+ memor(?:y|ies)|relevant memory)\s*$")
_HTML_TAG = re.compile(r"</?[a-zA-Z][^>]*>")
# Symbols a speaker would drop, said as words. Currency before a number moves after it.
_PREFIX_CURRENCY = re.compile(r"([$€₽])\s?(\d[\d\s.,]*\d|\d)")
_SYMBOL_WORDS = (("°C", " градусов"), ("°", " градусов"), ("€", " евро"), ("$", " долларов"),
                 ("₽", " рублей"), ("&", " и "))
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"(?:https?://|www\.)\S+")
# Bare hosts like t.me/x or docs.google.com/d/1: ASCII labels ending in a
# letter TLD, so "10.30" and "т.е." stay.
_BARE_HOST = re.compile(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}\b(?:/[^\s,;!?»)]*)?", re.ASCII | re.IGNORECASE)
_MARKS = re.compile(r"[*_`#>~|\[\]()]")
# Letters, digits, whitespace and the punctuation a voice reads naturally.
_NOT_SPOKEN = re.compile(r"[^\w\s.,:;!?«»\"'—–\-%№/+]")


def extract_spoken(content: str) -> tuple[str, str | None]:
    """The content without any 🔊 line, and the last such line's text."""
    kept, spoken = [], None
    for line in content.split("\n"):
        match = _SPOKEN_LINE.match(line)
        if match:
            spoken = match[1].strip() or None
        else:
            kept.append(line)
    return "\n".join(kept).rstrip(), spoken


def for_ear(text: str, limit: int = MAX_SPOKEN) -> str:
    """Plain text a speaker can read: no links, markup or emoji; at most ``limit``."""
    text = _HTML_TAG.sub("", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _URL.sub("", text)
    text = _BARE_HOST.sub("", text)
    text = _MARKS.sub("", text)
    text = _PREFIX_CURRENCY.sub(r"\2 \1", text)
    for symbol, words in _SYMBOL_WORDS:
        text = text.replace(symbol, words)
    text = _NOT_SPOKEN.sub("", text.replace("_", " "))
    text = re.sub(r"\s+([,.:;!?])", r"\1", text)
    text = " ".join(text.split()).strip(" «»\"'")
    if len(text) <= limit:
        return text
    cut = text[:limit + 1].rsplit(" ", 1)[0]
    return cut[:limit].rstrip(" ,;:—–-")


async def _aiohttp_post(url: str, body: bytes, headers: dict, timeout_s: float) -> dict | None:
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=timeout_s)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, data=body, headers=headers) as resp:
            if resp.status != 200:
                logger.warning("[alice_speech] /speak answered HTTP %s", resp.status)
                return None
            return await resp.json(content_type=None)


async def speak_then_strip(content: str, delivery: dict, *, metadata: dict | None = None,
                           post=None) -> str | None:
    """Speak the 🔊 line on the Station of an opted-in route; return what Telegram gets.

    ``send()`` also carries interim messages (a "stuck" warning, background
    review notes) marked ``_interim_send``: those are never spoken and get no
    🔇 line, only their 🔊 lines removed. ``None`` means "do not deliver":
    the memory recall line of a voice turn.
    """
    extra = delivery.get("deliver_extra") or {}
    if not isinstance(extra, dict) or "speak_on" not in extra:
        return content
    if _RECALL_NOTICE.match(content.strip()):
        return None  # not delivered at all (the user, 2026-11-08)
    stripped, spoken = extract_spoken(content)
    if (metadata or {}).get("_interim_send"):
        return stripped
    # Status notices (the Hindsight recall line, warnings) come through the
    # same send() with the same metadata as the answer. The route's answer
    # starts with «🎙 …» and ends with «🔊 …»; anything with neither is a
    # notice: not spoken, not marked (seen live 2026-11-08: the Station said
    # the fallback for "👁️ Hindsight — recalled 22 memories").
    if spoken is None and not stripped.lstrip().startswith(MIC_PREFIX):
        return content
    speaker = str(extra.get("speak_on") or "").strip()
    if not speaker or (speaker.startswith("{") and speaker.endswith("}")):
        return stripped
    try:
        secret = os.environ.get("ALICE_WEBHOOK_SECRET", "")
        if not secret:
            logger.warning("[alice_speech] ALICE_WEBHOOK_SECRET is not set; not speaking")
            return f"{stripped}\n\n{MUTE}"
        text = (for_ear(spoken) if spoken else "") or FALLBACK
        body = json.dumps({"speaker": speaker, "text": text}, ensure_ascii=False).encode()
        # alice-bridge checks "speak.<ts>.<body>" within 300 s, each signature once.
        ts = str(int(time.time()))
        signature = hmac.new(secret.encode(), b"speak." + ts.encode() + b"." + body, hashlib.sha256).hexdigest()
        headers = {"Content-Type": "application/json", "X-Speak-Timestamp": ts, "X-Speak-Signature": signature}
        url = os.environ.get("ALICE_SPEAK_URL", DEFAULT_URL)
        answer = await (post or _aiohttp_post)(url, body, headers, TIMEOUT_S)
        result = answer.get("result") if isinstance(answer, dict) else None
    except Exception as exc:  # speech must never cost the Telegram answer
        logger.warning("[alice_speech] speech failed: %s", type(exc).__name__)
        result = None
    logger.info("[alice_speech] route=%s speaker=%s result=%s", delivery.get("route"), speaker, result)
    if result in ("ok", "queued"):  # queued: spoken after the Station's current phrase
        return stripped
    if result == "auth_lost":
        return f"{stripped}\n\n{MUTE_AUTH}"
    return f"{stripped}\n\n{MUTE}"
