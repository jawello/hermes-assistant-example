"""Gmail message payload → plain text.

Kept apart from the API wrapper: real mail is a nested MIME tree and the
plain-text part is usually not at the top level, so this is the piece that
needs its own tests.
"""
import base64
import codecs
import html as html_module
import re

_SCRIPT_OR_STYLE = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES = re.compile(r"\n{3,}")
_CHARSET = re.compile(r'charset\s*=\s*["\']?([^\s"\'>;]+)', re.IGNORECASE)


def _extract_charset(part: dict) -> str:
    """Extract charset from part's Content-Type header, default to utf-8."""
    headers = part.get("headers") or []
    for header in headers:
        if header.get("name", "").lower() == "content-type":
            value = header.get("value", "")
            match = _CHARSET.search(value)
            if match:
                return match.group(1)
    return "utf-8"


def _decode(part: dict) -> str:
    """Decode base64 data from a part using its declared charset.

    Reads charset from the part's Content-Type header, falls back to UTF-8
    when absent or unknown. Uses errors="replace" as final fallback so an
    undeclared or mislabeled charset cannot raise; losing a character beats
    crashing the poller on one message.
    """
    data = (part.get("body") or {}).get("data")
    if not data:
        return ""

    charset = _extract_charset(part)
    raw_bytes = base64.urlsafe_b64decode(data.encode("ascii"))

    # Try the declared charset first
    try:
        codecs.lookup(charset)  # Validate charset is known
        return raw_bytes.decode(charset, "replace")
    except LookupError:
        # Unknown charset; fall back to UTF-8 with errors="replace"
        return raw_bytes.decode("utf-8", "replace")


def _strip_html(markup: str) -> str:
    without_comments = _HTML_COMMENT.sub(" ", markup)
    without_code = _SCRIPT_OR_STYLE.sub(" ", without_comments)
    text = _TAG.sub(" ", without_code)
    text = html_module.unescape(text)
    # &nbsp; unescapes to U+00A0, which is not matched by \s in the ASCII
    # class above; normalise it before collapsing runs.
    text = text.replace(" ", " ")
    text = _WHITESPACE.sub(" ", text)
    return _BLANK_LINES.sub("\n\n", text)


def extract_text(payload: dict, limit: int = 20000) -> str:
    """Plain text for a Gmail message payload.

    Prefers text/plain found anywhere in the tree; falls back to text/html with
    tags stripped. Returns "" when the message carries no text at all.
    """
    plain: list[str] = []
    markup: list[str] = []

    def walk(part: dict, depth: int = 0) -> None:
        # Limit recursion depth to 20 to prevent RecursionError on deeply nested
        # MIME trees, which would wedge the pipeline on retry.
        if depth > 20:
            return

        mime_type = part.get("mimeType", "")
        data = (part.get("body") or {}).get("data")
        if data:
            if mime_type == "text/plain":
                plain.append(_decode(part))
            elif mime_type == "text/html":
                markup.append(_decode(part))
        for sub in part.get("parts") or []:
            walk(sub, depth + 1)

    walk(payload)
    text = "\n".join(plain) if plain else _strip_html("\n".join(markup))
    return text.strip()[:limit]


def _part_header(part: dict, name: str) -> str:
    wanted = name.lower()
    for header in part.get("headers") or []:
        if header.get("name", "").lower() == wanted:
            return header.get("value", "")
    return ""


def list_attachments(payload: dict) -> list[dict]:
    """The files a message carries, as the user sees them in Gmail.

    A part with a filename is an attachment — except an image with a
    Content-ID: that is a logo or a signature the HTML pulls in via cid:, not
    a file anyone sent. Other inline parts stay; Apple Mail marks real PDFs
    inline.
    """
    found: list[dict] = []

    def walk(part: dict, depth: int = 0) -> None:
        if depth > 20:
            return
        filename = part.get("filename") or ""
        mime_type = part.get("mimeType", "")
        embedded = mime_type.startswith("image/") and _part_header(part, "Content-ID")
        if filename and not embedded:
            found.append(
                {
                    "part_id": part.get("partId", ""),
                    "filename": filename,
                    "mime_type": mime_type,
                    "size": int((part.get("body") or {}).get("size") or 0),
                }
            )
        for sub in part.get("parts") or []:
            walk(sub, depth + 1)

    walk(payload)
    return found


def find_part(payload: dict, part_id: str) -> dict | None:
    """The part with Gmail's `partId`, or None."""

    def walk(part: dict, depth: int = 0) -> dict | None:
        if depth > 20:
            return None
        if part.get("partId") == part_id:
            return part
        for sub in part.get("parts") or []:
            hit = walk(sub, depth + 1)
            if hit is not None:
                return hit
        return None

    return walk(payload)
