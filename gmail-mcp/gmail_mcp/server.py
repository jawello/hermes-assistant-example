"""MCP streamable-HTTP server exposing the user's mailbox to hermes.

The send path is gated here rather than in the agent's skill: a skill is an
instruction the model can reason its way around, this is code it cannot reach.
`gmail_send_draft` takes only the id of a draft that already exists, and asks
the human before sending it. `gmail_trash` and `gmail_delete_draft` ask the
same way; `gmail_archive` does not, since archived mail stays in the mailbox.
"""
import logging

from mcp.server.mcpserver import Context, MCPServer
from pydantic import BaseModel, Field

from gmail_mcp.gmail import AttachmentRefused, ExcludedMessage, WriteNotAuthorised, build_client

log = logging.getLogger("gmail.server")

# Bind on all interfaces so hermes reaches it at http://gmail-mcp:8000/mcp
# (streamable HTTP, not SSE — hermes issue #20349 drops the SSE session id).
mcp = MCPServer("gmail")

_gmail = None


def gmail():
    """Built lazily: a credential problem must not stop the server from
    starting, or hermes loses the whole toolset instead of one call."""
    global _gmail
    if _gmail is None:
        _gmail = build_client()
    return _gmail


class SendApproval(BaseModel):
    # Defaulted, not required. A client answers an elicitation with an action
    # and, optionally, field values; hermes' approval system asks the human
    # yes/no and collects none, so it replies accept with `content={}`. The
    # SDK validates that against this schema before the tool sees it, and a
    # required field made a real approval raise ValidationError instead of
    # sending. The consent lives in `action == "accept"` — this field only
    # gives a client that does collect it a way to say no.
    confirm: bool = Field(default=True, description="Send this email now?")


async def _refusal(ctx, question: str, schema, what: str) -> str | None:
    """Ask the human; None on an explicit yes, otherwise why not.

    Every path that is not an explicit yes is a refusal.
    """
    try:
        result = await ctx.elicit(message=question, schema=schema)
    except Exception as exc:  # noqa: BLE001
        # No channel to ask on is not permission.
        log.warning("elicitation failed (%s)", type(exc).__name__)
        # Name the exception: "could not be confirmed" on its own is true of
        # a client that cannot ask, one that timed out, and one whose answer
        # failed validation, and telling them apart meant reading the server
        # log twice.
        return f"the {what} could not be confirmed with the user ({type(exc).__name__})"

    if getattr(result, "action", None) != "accept":
        return f"not confirmed ({getattr(result, 'action', 'unknown')})"
    data = getattr(result, "data", None)
    if not isinstance(data, schema) or not data.confirm:
        return "not confirmed"
    return None


def human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1048576:
        return f"{size / 1024:.0f} KB"
    return f"{size / 1048576:.1f} MB"


async def send_draft_guarded(client, ctx, draft_id: str) -> dict:
    """Ask the human, then send. Every path that is not an explicit yes
    returns without sending."""
    try:
        draft = client.draft(draft_id)
    except Exception as exc:  # noqa: BLE001
        return {"sent": False, "reason": f"draft not found ({type(exc).__name__})"}

    # The files go on their own lines: what leaves the mailbox has to be
    # visible before the yes, not only the text.
    files = "".join(
        f"📎 {item['filename']} ({human_size(item['size'])})\n"
        for item in draft.get("attachments") or []
    )
    question = (
        "Send this email?\n"
        f"To: {draft['to']}\n"
        f"Subject: {draft['subject']}\n"
        f"{files}\n"
        f"{draft['body'][:2000]}"
    )
    refusal = await _refusal(ctx, question, SendApproval, "send")
    if refusal:
        return {"sent": False, "reason": refusal}

    client.send_draft(draft_id)
    return {"sent": True, "to": draft["to"], "subject": draft["subject"]}


# Per call. Enough for a day's inbox; a larger sweep is a filter's job, and
# the trash question has to stay readable on a phone.
MAX_TIDY = 100
# How many messages the trash question lists by name before "…and N more".
_LISTED = 15


class TrashApproval(BaseModel):
    # Defaulted for the same reason as SendApproval.confirm.
    confirm: bool = Field(default=True, description="Move these messages to Trash?")


def _tidy_request(client, message_ids: list[str]) -> tuple[list, int, str | None]:
    """The messages a tidy call may touch, how many it skipped, or why none."""
    ids = list(dict.fromkeys(message_ids))
    if not ids:
        return [], 0, "no message ids given"
    if len(ids) > MAX_TIDY:
        return [], 0, f"at most {MAX_TIDY} messages per call, got {len(ids)}"
    targets, skipped = client.tidy_targets(ids)
    return targets, skipped, None


def _tidy_result(key: str, done: int, skipped: int) -> dict:
    result = {key: done}
    if skipped:
        # A count only: a skipped message may be an excluded one, and nothing
        # about those leaves the server.
        result["skipped"] = skipped
    return result


def _write_failure(key: str, action, ids: list[str]) -> dict | None:
    """Run the write; a failure comes back as a result, never as a raise —
    three raising calls and hermes drops the whole server for the session."""
    try:
        action(ids)
    except WriteNotAuthorised as exc:
        return {key: 0, "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001
        log.warning("%s failed (%s)", key, type(exc).__name__)
        return {key: 0, "reason": f"Gmail refused the change ({type(exc).__name__})"}
    return None


def archive_guarded(client, message_ids: list[str]) -> dict:
    """Archive without asking: the mail only leaves the inbox and stays
    under All Mail, so a mistake costs one click to undo."""
    targets, skipped, refusal = _tidy_request(client, message_ids)
    if refusal:
        return {"archived": 0, "reason": refusal}
    ids = [meta.id for meta in targets]
    if ids:
        failure = _write_failure("archived", client.archive, ids)
        if failure:
            return failure
    return _tidy_result("archived", len(ids), skipped)


async def trash_guarded(client, ctx, message_ids: list[str]) -> dict:
    """Ask the human, then move to Trash. Every path that is not an explicit
    yes moves nothing."""
    targets, skipped, refusal = _tidy_request(client, message_ids)
    if refusal:
        return {"trashed": 0, "reason": refusal}
    if not targets:
        return _tidy_result("trashed", 0, skipped)

    lines = [f"• {meta.sender} — {meta.subject}" for meta in targets[:_LISTED]]
    if len(targets) > _LISTED:
        lines.append(f"…and {len(targets) - _LISTED} more")
    question = (
        f"Move {len(targets)} message(s) to Trash? Gmail keeps them there for 30 days.\n\n"
        + "\n".join(lines)
    )
    refusal = await _refusal(ctx, question, TrashApproval, "move to Trash")
    if refusal:
        return {"trashed": 0, "reason": refusal}

    ids = [meta.id for meta in targets]
    failure = _write_failure("trashed", client.trash, ids)
    if failure:
        return failure
    return _tidy_result("trashed", len(ids), skipped)


def create_draft_guarded(client, to: str, subject: str, body: str, attachments) -> dict:
    """A new draft, or why a file could not go into it."""
    try:
        return {"draft_id": client.create_draft(to, subject, body, attachments)}
    except AttachmentRefused as exc:
        return {"draft_id": None, "reason": str(exc)}


def create_reply_draft_guarded(client, message_id: str, body: str, attachments=()) -> dict:
    """Draft a reply inside the original thread, or a refusal.

    Like `read_message_guarded`, the refusal is a normal return value: a
    raising tool is a failed call, and three of those take the whole server
    out of service for the session.
    """
    try:
        return {"draft_id": client.create_reply_draft(message_id, body, attachments)}
    except ExcludedMessage as exc:
        return {"excluded": True, "reason": str(exc)}
    except AttachmentRefused as exc:
        return {"draft_id": None, "reason": str(exc)}


def list_drafts_guarded(client, query: str, limit: int) -> list[dict] | dict:
    """The drafts, or why there are none — never a raise."""
    try:
        return client.drafts(query, limit)
    except Exception as exc:  # noqa: BLE001
        log.warning("listing drafts failed (%s)", type(exc).__name__)
        return {"drafts": [], "reason": f"listing drafts failed ({type(exc).__name__})"}


def update_draft_guarded(client, draft_id: str, body: str, attachments) -> dict:
    """The draft's id after the change, or why it was not changed."""
    try:
        return {"draft_id": client.update_draft(draft_id, body, attachments)}
    except ExcludedMessage as exc:
        return {"excluded": True, "reason": str(exc)}
    except AttachmentRefused as exc:
        return {"draft_id": None, "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001
        log.warning("update_draft failed (%s)", type(exc).__name__)
        return {"draft_id": None, "reason": f"updating the draft failed ({type(exc).__name__})"}


class DeleteApproval(BaseModel):
    # Defaulted for the same reason as SendApproval.confirm.
    confirm: bool = Field(default=True, description="Delete this draft?")


async def delete_draft_guarded(client, ctx, draft_id: str) -> dict:
    """Ask the human, then delete. A deleted draft does not go to Trash, so
    every path that is not an explicit yes deletes nothing."""
    try:
        draft = client.draft(draft_id)
    except Exception as exc:  # noqa: BLE001
        return {"deleted": False, "reason": f"draft not found ({type(exc).__name__})"}

    files = "".join(
        f"📎 {item['filename']} ({human_size(item['size'])})\n"
        for item in draft.get("attachments") or []
    )
    question = (
        "Delete this draft? It does not go to Trash.\n"
        f"To: {draft['to']}\n"
        f"Subject: {draft['subject']}\n"
        f"{files}\n"
        f"{draft['body'][:2000]}"
    )
    refusal = await _refusal(ctx, question, DeleteApproval, "delete")
    if refusal:
        return {"deleted": False, "reason": refusal}

    try:
        client.delete_draft(draft_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("delete_draft failed (%s)", type(exc).__name__)
        return {"deleted": False, "reason": f"Gmail refused the delete ({type(exc).__name__})"}
    return {"deleted": True}


def save_attachment_guarded(client, message_id: str, part_id: str) -> dict:
    """Where the attachment was saved, or why not — never a raise."""
    try:
        return client.save_attachment(message_id, part_id)
    except ExcludedMessage as exc:
        return {"excluded": True, "reason": str(exc)}
    except AttachmentRefused as exc:
        return {"saved": False, "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001
        log.warning("save_attachment failed (%s)", type(exc).__name__)
        return {"saved": False, "reason": f"saving failed ({type(exc).__name__})"}


def read_message_guarded(client, message_id: str) -> dict:
    """Full text of one message, or a refusal.

    The refusal is a normal return value, not an exception: hermes counts a
    raising tool as a failed call and takes the server out of service after
    three of them.
    """
    try:
        meta = client.metadata(message_id)
        body, attachments = client.content(message_id)
    except ExcludedMessage as exc:
        return {"excluded": True, "reason": str(exc)}
    result = {
        "message_id": meta.id,
        "thread_id": meta.thread_id,
        "from": meta.sender,
        "subject": meta.subject,
        "date": meta.date,
        "body": body,
    }
    # Only when there are some: the cron passes read a lot of plain mail.
    if attachments:
        result["attachments"] = attachments
    return result


@mcp.tool()
def gmail_search(query: str, limit: int = 20) -> list[dict]:
    """Search the mailbox with Gmail query syntax (e.g. 'from:bank newer_than:7d').
    Returns message headers, not bodies."""
    return [
        {
            "message_id": m.id,
            "thread_id": m.thread_id,
            "from": m.sender,
            "subject": m.subject,
            "date": m.date,
        }
        for m in gmail().search(query, limit=limit)
    ]


@mcp.tool()
def gmail_read_message(message_id: str) -> dict:
    """Full text of one message, plus its files under "attachments" when it
    has any. Messages the user has excluded return {"excluded": true} and no
    content."""
    return read_message_guarded(gmail(), message_id)


@mcp.tool()
def gmail_read_thread(thread_id: str) -> list[dict]:
    """Every message in a thread, oldest first, with "attachments" on the
    messages that have files."""
    out = []
    for meta, body, attachments in gmail().thread(thread_id):
        entry = {"from": meta.sender, "subject": meta.subject, "date": meta.date, "body": body}
        if attachments:
            entry["message_id"] = meta.id
            entry["attachments"] = attachments
        out.append(entry)
    return out


@mcp.tool()
def gmail_create_draft(to: str, subject: str, body: str, attachments: list[str] | None = None) -> dict:
    """Write a draft. This does not send anything — show the text to the user,
    then call gmail_send_draft with the id returned here. attachments: paths of
    files under /shared, 25 MB in total — copy Telegram and Drive files into
    /shared/outbox/ first; another email's file comes from gmail_save_attachment."""
    return create_draft_guarded(gmail(), to, subject, body, attachments or [])


@mcp.tool()
def gmail_create_reply_draft(message_id: str, body: str, attachments: list[str] | None = None) -> dict:
    """Write a reply to an existing message, inside its thread. Use this
    instead of gmail_create_draft whenever answering mail the user already has:
    the recipient, the subject and the threading headers come from the
    original. This does not send anything — show the text to the user, then
    call gmail_send_draft with the id returned here. attachments: as for
    gmail_create_draft."""
    return create_reply_draft_guarded(gmail(), message_id, body, attachments or [])


@mcp.tool()
def gmail_list_drafts(query: str = "", limit: int = 10) -> list[dict] | dict:
    """the user's drafts, newest first, with draft_id, thread_id, to, subject,
    the full text and the files. query: Gmail search syntax applied to drafts
    (e.g. 'acme', 'to:support@example.com'); empty lists the latest ones."""
    return list_drafts_guarded(gmail(), query, limit)


@mcp.tool()
def gmail_update_draft(draft_id: str, body: str, attachments: list[str] | None = None) -> dict:
    """Change an existing draft instead of writing a second one. body replaces
    the whole text; the draft keeps its id, recipients, subject and thread.
    attachments: omit to keep the files it has; a list of /shared paths
    replaces them ([] removes them all)."""
    return update_draft_guarded(gmail(), draft_id, body, attachments)


@mcp.tool()
async def gmail_delete_draft(draft_id: str, ctx: Context) -> dict:
    """Delete a draft. The user is asked to confirm first; if they decline,
    or cannot be asked, nothing is deleted. Deleted drafts do not go to Trash."""
    return await delete_draft_guarded(gmail(), ctx, draft_id)


@mcp.tool()
def gmail_save_attachment(message_id: str, part_id: str) -> dict:
    """Save one attachment (part_id from the message's attachments list) under
    /shared/mail/ and return its path: to read it, upload it to Drive, send it
    in Telegram (MEDIA:<path>) or attach it to a draft."""
    return save_attachment_guarded(gmail(), message_id, part_id)


@mcp.tool()
async def gmail_send_draft(draft_id: str, ctx: Context) -> dict:
    """Send an existing draft. The user is asked to confirm first; if they
    decline, or cannot be asked, nothing is sent."""
    return await send_draft_guarded(gmail(), ctx, draft_id)


@mcp.tool()
def gmail_archive(message_ids: list[str]) -> dict:
    """Archive messages: take them out of the inbox. They stay under All Mail
    and in search. Up to 100 ids per call, from gmail_search. Does not ask the
    user — archiving is undone with one click."""
    return archive_guarded(gmail(), message_ids)


@mcp.tool()
async def gmail_trash(message_ids: list[str], ctx: Context) -> dict:
    """Move messages to Trash (Gmail keeps them 30 days, then deletes them).
    Up to 100 ids per call, from gmail_search. The user is asked to confirm
    first; if they decline, or cannot be asked, nothing moves. There is no
    permanent delete."""
    return await trash_guarded(gmail(), ctx, message_ids)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
