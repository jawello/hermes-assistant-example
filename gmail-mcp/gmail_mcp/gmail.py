"""Thin wrapper over the Gmail API.

`GmailClient` takes an already-built discovery service so tests can inject a
fake; `build_client` is the only function here that touches the network.
"""
import base64
import email
import email.policy
import logging
import mimetypes
import re
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path

from gmail_mcp.auth import load_credentials
from gmail_mcp.exclusions import SKIP_LABEL, Exclusions, excluded_reason, load_exclusions
from gmail_mcp.mime import extract_text, find_part, list_attachments

log = logging.getLogger("gmail.client")

USER = "me"

_UNSET = object()

# What Gmail lets one message carry in attachments.
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
_NAME_BYTES = 200
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# Book types Python 3.12 does not know without a system /etc/mime.types (the
# slim image has none): sent as application/octet-stream, an epub never
# reached the PocketBook through Send-to-PocketBook.
_BOOK_TYPES = {".epub": "application/epub+zip", ".fb2": "application/x-fictionbook+xml"}
# Parsing a draft to change it: headers that are not touched go back out
# exactly as Gmail stored them, never refolded.
_KEEP_HEADERS = email.policy.default.clone(refold_source="none")


class ExcludedMessage(Exception):
    """The message matches an exclusion rule.

    Nothing about it may leave the VPS — not the body, not the subject, not
    the sender. The exception carries the reason, which names the rule and
    never the message.
    """


class WriteNotAuthorised(Exception):
    """Gmail refused a write because the refresh token lacks gmail.modify.

    Reads keep working with such a token, so without its own name this looks
    like a broken mailbox rather than a missing grant.
    """


class AttachmentRefused(Exception):
    """A file the draft cannot take, or an attachment that cannot be saved.

    The message is for hermes: it says what is wrong and what to do instead.
    """


def safe_name(name: str) -> str:
    """A sender-chosen file name reduced to one harmless path component."""
    name = _CONTROL.sub("", name).replace("/", "_").replace("\\", "_").strip().lstrip(".")
    if len(name.encode("utf-8")) > _NAME_BYTES:
        stem, dot, ext = name.rpartition(".")
        ext = f".{ext}" if dot and stem and len(ext.encode("utf-8")) <= 16 else ""
        body = stem if ext else name
        budget = _NAME_BYTES - len(ext.encode("utf-8"))
        name = body.encode("utf-8")[:budget].decode("utf-8", "ignore") + ext
    return name or "attachment"


@dataclass(frozen=True)
class MessageMeta:
    id: str
    thread_id: str
    sender: str
    subject: str
    date: str
    list_id: str
    label_ids: list[str] = field(default_factory=list)
    snippet: str = ""


def _header(payload: dict, name: str) -> str:
    """Header value, matched case-insensitively per RFC 5322."""
    wanted = name.lower()
    for entry in payload.get("headers") or []:
        if entry.get("name", "").lower() == wanted:
            return entry.get("value", "")
    return ""


def _reply_subject(subject: str) -> str:
    """Prefix `subject` with Re:, unless it already carries one.

    Gmail groups by subject as well as by References, so the prefix is part
    of threading — and a stacked "Re: Re: x" reads as sloppy to the recipient.
    """
    subject = subject.strip()
    if subject.lower().startswith("re:"):
        return subject
    return f"Re: {subject}" if subject else "Re:"


def _meta_from(message: dict, thread_id: str = "") -> MessageMeta:
    payload = message.get("payload") or {}
    return MessageMeta(
        id=message["id"],
        thread_id=thread_id or message.get("threadId", ""),
        sender=_header(payload, "From"),
        subject=_header(payload, "Subject"),
        date=_header(payload, "Date"),
        list_id=_header(payload, "List-Id"),
        label_ids=list(message.get("labelIds") or []),
        snippet=message.get("snippet", ""),
    )


class GmailClient:
    def __init__(self, service, http_error=None, rules=None, shared_dir=None):
        self._service = service
        # Where files are handed between containers (/shared in compose).
        self._shared_dir = Path(shared_dir or "/shared")
        if http_error is None:
            from googleapiclient.errors import HttpError

            http_error = HttpError
        self._http_error = http_error
        self._rules = rules if rules is not None else Exclusions()
        # Both are one round-trip each and never change during a process's
        # life. The poller fetched them once per cycle; nothing does that now,
        # so the client caches them itself rather than paying per read.
        self._skip_label_id = _UNSET
        self._self_address = _UNSET

    def _reason(self, meta: MessageMeta, *, ignore_self: bool = False) -> str | None:
        """Why this message must not be read, or None.

        `ignore_self` disables only the "self" rule, which exists to keep
        the user's own outgoing mail from being treated as something new to
        react to. Inside a thread that rule is actively harmful: his replies
        are often the half that says what was decided. The privacy rules —
        skip label, denied sender, denied domain — are unaffected and still
        apply to every message everywhere.
        """
        if self._skip_label_id is _UNSET:
            self._skip_label_id = self.label_id(SKIP_LABEL)
        if self._self_address is _UNSET:
            self._self_address = self.profile_address()
        return excluded_reason(
            meta,
            self._rules,
            skip_label_id=self._skip_label_id,
            self_address="" if ignore_self else self._self_address,
        )

    def _guard(self, meta: MessageMeta, *, ignore_self: bool = False) -> MessageMeta:
        reason = self._reason(meta, ignore_self=ignore_self)
        if reason:
            # The id and the reason only — never the sender or the subject of
            # a message the user asked us not to look at.
            log.info("excluded %s (%s)", meta.id, reason)
            raise ExcludedMessage(reason)
        return meta

    # ── reading ──────────────────────────────────────────────
    def profile_address(self) -> str:
        return self._service.users().getProfile(userId=USER).execute()["emailAddress"]

    def _metadata_raw(self, message_id: str) -> MessageMeta:
        message = (
            self._service.users()
            .messages()
            .get(
                userId=USER,
                id=message_id,
                format="metadata",
                metadataHeaders=["From", "Subject", "Date", "List-Id"],
            )
            .execute()
        )
        return _meta_from(message)

    def metadata(self, message_id: str) -> MessageMeta:
        return self._guard(self._metadata_raw(message_id))

    def _full(self, message_id: str) -> dict:
        return (
            self._service.users()
            .messages()
            .get(userId=USER, id=message_id, format="full")
            .execute()
        )

    def content(self, message_id: str) -> tuple[str, list[dict]]:
        """Text and attachment list of one message, from one round-trip.

        format="full" already carries headers and labelIds, so the guard
        costs no extra round-trip here.
        """
        message = self._full(message_id)
        self._guard(_meta_from(message))
        payload = message.get("payload") or {}
        return extract_text(payload), list_attachments(payload)

    def body(self, message_id: str) -> str:
        return self.content(message_id)[0]

    def search(self, query: str, limit: int = 20) -> list[MessageMeta]:
        response = (
            self._service.users()
            .messages()
            .list(userId=USER, q=query, maxResults=limit)
            .execute()
        )
        out: list[MessageMeta] = []
        for item in response.get("messages") or []:
            meta = self._metadata_raw(item["id"])
            reason = self._reason(meta)
            if reason is None:
                out.append(meta)
            else:
                # The reason names the rule, never the message, so it is safe
                # to log — and without it a dropped message is unexplainable.
                log.info("excluded %s from search results (%s)", meta.id, reason)
        return out

    def thread(self, thread_id: str) -> list[tuple[MessageMeta, str, list[dict]]]:
        response = (
            self._service.users().threads().get(userId=USER, id=thread_id, format="full").execute()
        )
        out: list[tuple[MessageMeta, str]] = []
        for message in response.get("messages") or []:
            meta = _meta_from(message, thread_id)
            # Per message, not per thread: the rule is about who wrote, and a
            # thread can mix an excluded sender with ordinary ones. the user's
            # own replies stay — see _reason's ignore_self.
            if self._reason(meta, ignore_self=True) is not None:
                log.info("excluded %s from thread %s", meta.id, thread_id)
                continue
            payload = message.get("payload") or {}
            out.append((meta, extract_text(payload), list_attachments(payload)))
        return out

    def save_attachment(self, message_id: str, part_id: str) -> dict:
        """Write one attachment to <shared>/mail/<message_id>/ and say where.

        Guarded like a read. `ignore_self`: forwarding a file he sent last
        week is ordinary.
        """
        message = self._full(message_id)
        self._guard(_meta_from(message), ignore_self=True)
        payload = message.get("payload") or {}
        part = find_part(payload, part_id)
        if part is None or not part.get("filename"):
            raise AttachmentRefused(f"the message has no attachment with part_id {part_id!r}")
        filename = part["filename"]
        name = safe_name(filename)
        # image001.png twice in one message is common; keep both.
        if [a["filename"] for a in list_attachments(payload)].count(filename) > 1:
            name = safe_name(f"{part_id}_{filename}")

        body = part.get("body") or {}
        data = body.get("data")
        if not data and body.get("attachmentId"):
            data = (
                self._service.users()
                .messages()
                .attachments()
                .get(userId=USER, messageId=message_id, id=body["attachmentId"])
                .execute()["data"]
            )
        data = data or ""
        content = base64.urlsafe_b64decode((data + "=" * (-len(data) % 4)).encode("ascii"))

        folder = self._shared_dir / "mail" / safe_name(message_id)
        target = folder / name
        if not target.resolve().is_relative_to(folder.resolve()):
            raise AttachmentRefused("the attachment name would leave the mail folder")
        folder.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return {
            "path": str(target),
            "filename": name,
            "mime_type": part.get("mimeType", ""),
            "size": len(content),
        }

    def label_id(self, name: str) -> str | None:
        response = self._service.users().labels().list(userId=USER).execute()
        for label in response.get("labels") or []:
            if label.get("name") == name:
                return label.get("id")
        return None

    # ── writing ──────────────────────────────────────────────
    def _attachment_files(self, paths) -> list[tuple[str, Path]]:
        """Each path as (name in the mail, resolved file), or AttachmentRefused.

        Only files under the shared folder: hermes puts them there, and
        nothing else on this container is anyone's to send. resolve() follows
        symlinks, so a link pointing out is caught by the same check.
        """
        root = self._shared_dir.resolve()
        files: list[tuple[str, Path]] = []
        total = 0
        for raw in paths:
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = self._shared_dir / candidate
            try:
                resolved = candidate.resolve()
            except (OSError, RuntimeError):
                raise AttachmentRefused(f"{raw}: cannot be resolved") from None
            if not resolved.is_relative_to(root):
                raise AttachmentRefused(f"{raw}: only files under {self._shared_dir} can be attached")
            if not resolved.is_file():
                raise AttachmentRefused(f"{raw}: no such file")
            total += resolved.stat().st_size
            files.append((candidate.name, resolved))
        if total > MAX_ATTACHMENT_BYTES:
            raise AttachmentRefused(
                f"the files add up to {total / 1048576:.1f} MB and Gmail takes 25 MB: "
                "share the file on Google Drive and put the link in the body instead"
            )
        return files

    @staticmethod
    def _attach(message: EmailMessage, files: list[tuple[str, Path]]) -> None:
        for name, path in files:
            mime_type = _BOOK_TYPES.get(Path(name).suffix.lower()) or mimetypes.guess_type(name)[0]
            maintype, _, subtype = (mime_type or "application/octet-stream").partition("/")
            message.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=name)

    def _create_draft(self, message: EmailMessage, *, thread_id: str = "", upload: bool = False) -> str:
        """Store the draft. With files it goes up as message/rfc822 media: a
        raw message inside the JSON body is bounded by the request size, an
        upload only by Gmail's message limit."""
        meta = {"threadId": thread_id} if thread_id else {}
        drafts = self._service.users().drafts()
        if upload:
            from googleapiclient.http import MediaInMemoryUpload

            media = MediaInMemoryUpload(message.as_bytes(), mimetype="message/rfc822", resumable=True)
            request = drafts.create(userId=USER, body={"message": meta}, media_body=media)
        else:
            meta["raw"] = base64.urlsafe_b64encode(message.as_bytes()).decode()
            request = drafts.create(userId=USER, body={"message": meta})
        return request.execute()["id"]

    def create_draft(self, to: str, subject: str, body: str, attachments=()) -> str:
        # Files first: a refused path must not leave a half-made draft behind.
        files = self._attachment_files(attachments)
        message = EmailMessage()
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        self._attach(message, files)
        return self._create_draft(message, upload=bool(files))

    def create_reply_draft(self, message_id: str, body: str, attachments=()) -> str:
        """Draft an answer that stays in `message_id`'s thread.

        Gmail files a draft by `threadId`, and every other mail client by
        In-Reply-To/References. Both are set here: a "Re:" subject alone is
        not threading, which is how a reply to a sender ended up as a new
        conversation on 2026-09-21.
        """
        files = self._attachment_files(attachments)
        source = self._reply_source(message_id)
        message = EmailMessage()
        message["To"] = source["to"]
        message["Subject"] = _reply_subject(source["subject"])
        if source["message_id"]:
            message["In-Reply-To"] = source["message_id"]
            # RFC 5322: the parent's chain, then the parent. Replacing the
            # chain instead of extending it orphans the reply in long threads.
            message["References"] = (
                f"{source['references']} {source['message_id']}".strip()
            )
        message.set_content(body)
        self._attach(message, files)
        return self._create_draft(message, thread_id=source["thread_id"], upload=bool(files))

    def _reply_source(self, message_id: str) -> dict:
        """What answering `message_id` needs: where to send it, and what makes
        it part of the same conversation.

        One metadata round-trip, guarded like every other read — the privacy
        rules gate writing too. `ignore_self` because following up on his own
        last message in a thread is ordinary, and the "self" rule exists to
        stop his outgoing mail looking like news, not to stop him replying.
        """
        message = (
            self._service.users()
            .messages()
            .get(
                userId=USER,
                id=message_id,
                format="metadata",
                metadataHeaders=[
                    "From",
                    "Reply-To",
                    "Subject",
                    "Date",
                    "List-Id",
                    "Message-ID",
                    "References",
                ],
            )
            .execute()
        )
        self._guard(_meta_from(message), ignore_self=True)
        payload = message.get("payload") or {}
        return {
            "thread_id": message.get("threadId", ""),
            "to": _header(payload, "Reply-To") or _header(payload, "From"),
            "subject": _header(payload, "Subject"),
            "message_id": _header(payload, "Message-ID"),
            "references": _header(payload, "References"),
        }

    def draft(self, draft_id: str) -> dict:
        """The draft's recipient, subject, text and files — what the user is
        shown before being asked to approve the send."""
        response = (
            self._service.users()
            .drafts()
            .get(userId=USER, id=draft_id, format="full")
            .execute()
        )
        payload = (response.get("message") or {}).get("payload") or {}
        return {
            "to": _header(payload, "To"),
            "subject": _header(payload, "Subject"),
            "body": extract_text(payload),
            "attachments": list_attachments(payload),
        }

    def drafts(self, query: str = "", limit: int = 10) -> list[dict]:
        """the user's drafts, newest first, each with its text and files.

        Guarded like a read, except for the "self" rule: every draft is his.
        """
        response = (
            self._service.users()
            .drafts()
            .list(userId=USER, q=query, maxResults=limit)
            .execute()
        )
        out = []
        for item in response.get("drafts") or []:
            draft = (
                self._service.users()
                .drafts()
                .get(userId=USER, id=item["id"], format="full")
                .execute()
            )
            message = draft.get("message") or {}
            meta = _meta_from(message)
            reason = self._reason(meta, ignore_self=True)
            if reason:
                log.info("excluded draft %s from the list (%s)", item["id"], reason)
                continue
            payload = message.get("payload") or {}
            out.append(
                {
                    "draft_id": item["id"],
                    "message_id": meta.id,
                    "thread_id": meta.thread_id,
                    "to": _header(payload, "To"),
                    "subject": meta.subject,
                    "body": extract_text(payload),
                    "attachments": list_attachments(payload),
                }
            )
        return out

    def update_draft(self, draft_id: str, body: str, attachments=None) -> str:
        """Replace a draft's text, keeping its id, recipients, subject, thread
        and threading headers.

        `attachments=None` keeps the files the draft has; a list replaces
        them. A draft written in Gmail with an HTML part comes back as plain
        text.
        """
        files = None if attachments is None else self._attachment_files(attachments)
        draft = (
            self._service.users()
            .drafts()
            .get(userId=USER, id=draft_id, format="raw")
            .execute()
        )
        stored = draft.get("message") or {}
        raw = stored.get("raw", "")
        message = email.message_from_bytes(
            base64.urlsafe_b64decode((raw + "=" * (-len(raw) % 4)).encode("ascii")),
            policy=_KEEP_HEADERS,
        )
        self._guard(
            MessageMeta(
                id=stored.get("id", ""),
                thread_id=stored.get("threadId", ""),
                sender=str(message.get("From", "")),
                subject=str(message.get("Subject", "")),
                date=str(message.get("Date", "")),
                list_id=str(message.get("List-Id", "")),
                label_ids=list(stored.get("labelIds") or []),
            ),
            ignore_self=True,
        )
        kept = (
            [(part.get_filename(), part.get_content_type(), part.get_payload(decode=True))
             for part in message.iter_attachments()]
            if files is None
            else []
        )
        # Drops the body and the Content-* headers, keeps every other header.
        message.clear_content()
        message.set_content(body)
        for name, content_type, data in kept:
            maintype, _, subtype = content_type.partition("/")
            message.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
        self._attach(message, files or [])

        meta = {"threadId": stored["threadId"]} if stored.get("threadId") else {}
        drafts = self._service.users().drafts()
        if kept or files:
            from googleapiclient.http import MediaInMemoryUpload

            media = MediaInMemoryUpload(message.as_bytes(), mimetype="message/rfc822", resumable=True)
            request = drafts.update(userId=USER, id=draft_id, body={"message": meta}, media_body=media)
        else:
            meta["raw"] = base64.urlsafe_b64encode(message.as_bytes()).decode()
            request = drafts.update(userId=USER, id=draft_id, body={"message": meta})
        return request.execute()["id"]

    def delete_draft(self, draft_id: str) -> None:
        """Delete a draft for good: Gmail does not put drafts in Trash."""
        self._service.users().drafts().delete(userId=USER, id=draft_id).execute()

    def send_draft(self, draft_id: str) -> None:
        self._service.users().drafts().send(userId=USER, body={"id": draft_id}).execute()

    # ── tidying ──────────────────────────────────────────────
    def tidy_targets(self, message_ids: list[str]) -> tuple[list[MessageMeta], int]:
        """The messages that may be archived or trashed, and how many were not.

        The privacy rules gate writing as they gate reading — an excluded
        message is not touched at all. An id that no longer exists is skipped
        too, so one stale id does not fail the rest. `ignore_self`: tidying
        away his own sent mail is ordinary.
        """
        targets: list[MessageMeta] = []
        skipped = 0
        for message_id in message_ids:
            try:
                meta = self._metadata_raw(message_id)
            except self._http_error:
                log.info("skipped %s: not readable", message_id)
                skipped += 1
                continue
            reason = self._reason(meta, ignore_self=True)
            if reason is None:
                targets.append(meta)
            else:
                log.info("skipped %s (%s)", message_id, reason)
                skipped += 1
        return targets, skipped

    def archive(self, message_ids: list[str]) -> None:
        """Take the messages out of the inbox. They stay under All Mail."""
        self._write(
            self._service.users()
            .messages()
            .batchModify(userId=USER, body={"ids": list(message_ids), "removeLabelIds": ["INBOX"]})
        )

    def trash(self, message_ids: list[str]) -> None:
        """Move the messages to Trash, where Gmail keeps them for 30 days.

        messages.trash, never messages.delete: the scope this server holds
        cannot delete permanently, and neither can this code.
        """
        for message_id in message_ids:
            self._write(self._service.users().messages().trash(userId=USER, id=message_id))

    def _write(self, request) -> None:
        try:
            request.execute()
        except self._http_error as exc:
            if getattr(getattr(exc, "resp", None), "status", None) == 403:
                raise WriteNotAuthorised(
                    "Gmail refused the change: the stored Google token has no "
                    "permission to modify mail. the user has to re-run the Google "
                    "login once (python -m gmail_mcp.auth) and update "
                    "GMAIL_REFRESH_TOKEN."
                ) from None
            raise


def build_client(env=None) -> GmailClient:
    import os

    from googleapiclient.discovery import build

    credentials = load_credentials(env)
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    rules = load_exclusions(os.environ.get("GMAIL_EXCLUSIONS", "/opt/gmail-mcp/exclusions.yaml"))
    return GmailClient(service, rules=rules, shared_dir=os.environ.get("SHARED_DIR", "/shared"))
