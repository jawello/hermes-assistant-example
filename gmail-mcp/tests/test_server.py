import pytest

from gmail_mcp.gmail import AttachmentRefused, ExcludedMessage, MessageMeta, WriteNotAuthorised
from gmail_mcp.server import (
    MAX_TIDY,
    SendApproval,
    TrashApproval,
    archive_guarded,
    create_draft_guarded,
    create_reply_draft_guarded,
    DeleteApproval,
    delete_draft_guarded,
    list_drafts_guarded,
    update_draft_guarded,
    human_size,
    read_message_guarded,
    save_attachment_guarded,
    send_draft_guarded,
    trash_guarded,
)


class FakeGmail:
    def __init__(self):
        self.sent = []

    def draft(self, draft_id):
        return {"to": "someone@example.com", "subject": "Re: invoice", "body": "Text"}

    def send_draft(self, draft_id):
        self.sent.append(draft_id)


class FakeResult:
    def __init__(self, action, data=None):
        self.action = action
        self.data = data


class FakeContext:
    def __init__(self, result):
        self._result = result
        self.messages = []

    async def elicit(self, message, schema):
        self.messages.append(message)
        return self._result


async def test_accepting_sends_the_draft():
    gmail, ctx = FakeGmail(), FakeContext(FakeResult("accept", SendApproval(confirm=True)))
    result = await send_draft_guarded(gmail, ctx, "d1")
    assert gmail.sent == ["d1"]
    assert result["sent"] is True


async def test_declining_sends_nothing():
    gmail, ctx = FakeGmail(), FakeContext(FakeResult("decline"))
    result = await send_draft_guarded(gmail, ctx, "d1")
    assert gmail.sent == []
    assert result["sent"] is False


async def test_cancelling_sends_nothing():
    gmail, ctx = FakeGmail(), FakeContext(FakeResult("cancel"))
    assert (await send_draft_guarded(gmail, ctx, "d1"))["sent"] is False
    assert gmail.sent == []


async def test_accepting_with_confirm_false_sends_nothing():
    # "accept" only means the form came back. The field is the answer.
    gmail, ctx = FakeGmail(), FakeContext(FakeResult("accept", SendApproval(confirm=False)))
    assert (await send_draft_guarded(gmail, ctx, "d1"))["sent"] is False
    assert gmail.sent == []


async def test_an_unrecognised_action_sends_nothing():
    # Fail closed. An unknown action is not consent.
    gmail, ctx = FakeGmail(), FakeContext(FakeResult("something-new"))
    assert (await send_draft_guarded(gmail, ctx, "d1"))["sent"] is False
    assert gmail.sent == []


async def test_an_elicitation_failure_sends_nothing():
    class Broken:
        async def elicit(self, message, schema):
            raise RuntimeError("client does not support elicitation")

    gmail = FakeGmail()
    result = await send_draft_guarded(gmail, Broken(), "d1")
    assert gmail.sent == []
    assert result["sent"] is False
    assert "could not be confirmed" in result["reason"]


async def test_the_question_names_the_recipient_and_subject():
    # The human has to be able to answer without going to look it up.
    ctx = FakeContext(FakeResult("decline"))
    await send_draft_guarded(FakeGmail(), ctx, "d1")
    assert "someone@example.com" in ctx.messages[0]
    assert "Re: invoice" in ctx.messages[0]


async def test_a_missing_draft_sends_nothing():
    class MissingDraftGmail(FakeGmail):
        def draft(self, draft_id):
            raise KeyError("no such draft")

    gmail = MissingDraftGmail()
    ctx = FakeContext(FakeResult("accept", SendApproval(confirm=True)))
    result = await send_draft_guarded(gmail, ctx, "d1")
    assert gmail.sent == []
    assert result["sent"] is False
    assert "not found" in result["reason"]


async def test_accepting_with_no_data_sends_nothing():
    gmail, ctx = FakeGmail(), FakeContext(FakeResult("accept", None))
    assert (await send_draft_guarded(gmail, ctx, "d1"))["sent"] is False
    assert gmail.sent == []


async def test_accepting_with_an_impostor_data_sends_nothing():
    # Only a real SendApproval counts. An object that merely quacks like one
    # (a truthy `confirm` attribute) is not consent — pins the isinstance guard.
    class Impostor:
        confirm = True

    gmail, ctx = FakeGmail(), FakeContext(FakeResult("accept", Impostor()))
    assert (await send_draft_guarded(gmail, ctx, "d1"))["sent"] is False
    assert gmail.sent == []


class ExcludingGmail:
    def metadata(self, message_id):
        raise ExcludedMessage("denied domain")

    def body(self, message_id):
        raise ExcludedMessage("denied domain")

    def content(self, message_id):
        raise ExcludedMessage("denied domain")


def test_reading_an_excluded_message_returns_a_marker_not_an_exception():
    # An MCP tool that raises counts as a failed call, and three failed calls
    # trip hermes' circuit breaker and take the whole server away for the
    # session. A refusal is a normal result, so it is returned as one.
    result = read_message_guarded(ExcludingGmail(), "m9")
    assert result == {"excluded": True, "reason": "denied domain"}


class ReplyingGmail:
    def __init__(self):
        self.calls = []

    def create_reply_draft(self, message_id, body, attachments=()):
        self.calls.append((message_id, body))
        return "d7"


def test_a_reply_draft_returns_its_id():
    gmail = ReplyingGmail()
    assert create_reply_draft_guarded(gmail, "m20", "Jessie is better") == {"draft_id": "d7"}
    assert gmail.calls == [("m20", "Jessie is better")]


def test_replying_to_an_excluded_message_returns_a_marker_not_an_exception():
    # Same reason as reading: a raising tool is a failed call, and three of
    # them take the gmail server out of service for the rest of the session.
    class Excluding:
        def create_reply_draft(self, message_id, body, attachments=()):
            raise ExcludedMessage("denied domain")

    result = create_reply_draft_guarded(Excluding(), "m9", "text")
    assert result == {"excluded": True, "reason": "denied domain"}


def test_an_approval_that_carries_no_fields_is_still_consent():
    # hermes answers an elicitation with action="accept" and content={}: its
    # approval system asks the human yes/no and collects no field values. The
    # SDK validates that {} against this schema before send_draft_guarded ever
    # sees it (mcp/server/elicitation.py: `schema.model_validate(content)`),
    # so a required `confirm` turned a real approval into a ValidationError
    # and sent nothing — 2026-09-21, the second failed send to a sender.
    assert SendApproval.model_validate({}).confirm is True


async def test_a_fieldless_approval_sends_the_draft():
    gmail = FakeGmail()
    ctx = FakeContext(FakeResult("accept", SendApproval.model_validate({})))
    assert (await send_draft_guarded(gmail, ctx, "d1"))["sent"] is True
    assert gmail.sent == ["d1"]


async def test_an_explicit_no_still_sends_nothing():
    # The default must not swallow a client that does collect the field and
    # comes back with false.
    gmail = FakeGmail()
    ctx = FakeContext(FakeResult("accept", SendApproval(confirm=False)))
    assert (await send_draft_guarded(gmail, ctx, "d1"))["sent"] is False
    assert gmail.sent == []


async def test_an_elicitation_failure_names_what_went_wrong():
    # "could not be confirmed" alone cost two rounds of log-reading to place.
    # The exception type is not sensitive and says which half broke.
    class Broken:
        async def elicit(self, message, schema):
            raise ValueError("boom")

    result = await send_draft_guarded(FakeGmail(), Broken(), "d1")
    assert result["sent"] is False
    assert "ValueError" in result["reason"]


# ── tidying: archive and trash ───────────────────────────


def _meta(message_id, sender="Shop <news@shop.example>", subject="Sale"):
    return MessageMeta(
        id=message_id, thread_id="t", sender=sender, subject=subject, date="", list_id=""
    )


class TidyingGmail:
    """Every id is tidyable except the ones listed in `skip`."""

    def __init__(self, skip=(), write_error=None):
        self.skip = set(skip)
        self.write_error = write_error
        self.archived = []
        self.trashed = []

    def tidy_targets(self, message_ids):
        targets = [_meta(i, subject=f"Subject {i}") for i in message_ids if i not in self.skip]
        return targets, len(message_ids) - len(targets)

    def archive(self, message_ids):
        if self.write_error:
            raise self.write_error
        self.archived.extend(message_ids)

    def trash(self, message_ids):
        if self.write_error:
            raise self.write_error
        self.trashed.extend(message_ids)


def test_archive_needs_no_confirmation_and_reports_the_count():
    # Archiving only takes the mail out of the inbox; it stays under All Mail.
    gmail = TidyingGmail()
    assert archive_guarded(gmail, ["m1", "m2"]) == {"archived": 2}
    assert gmail.archived == ["m1", "m2"]


def test_archive_reports_what_it_skipped_without_naming_it():
    gmail = TidyingGmail(skip={"m9"})
    assert archive_guarded(gmail, ["m1", "m9"]) == {"archived": 1, "skipped": 1}
    assert gmail.archived == ["m1"]


def test_archive_deduplicates_ids():
    gmail = TidyingGmail()
    assert archive_guarded(gmail, ["m1", "m1"]) == {"archived": 1}


@pytest.mark.parametrize("ids", [[], [f"m{i}" for i in range(MAX_TIDY + 1)]])
def test_archive_refuses_an_empty_or_oversized_batch(ids):
    gmail = TidyingGmail()
    result = archive_guarded(gmail, ids)
    assert result["archived"] == 0
    assert "reason" in result
    assert gmail.archived == []


def test_archive_without_write_access_returns_the_reason_instead_of_raising():
    # A raising tool is a failed call; three of them and hermes drops the
    # whole gmail server for the session, reading included.
    gmail = TidyingGmail(write_error=WriteNotAuthorised("re-run the Google login"))
    result = archive_guarded(gmail, ["m1"])
    assert result == {"archived": 0, "reason": "re-run the Google login"}


def test_archive_failure_is_a_result_not_an_exception():
    gmail = TidyingGmail(write_error=RuntimeError("boom"))
    result = archive_guarded(gmail, ["m1"])
    assert result["archived"] == 0
    assert "RuntimeError" in result["reason"]


async def test_trash_asks_first_and_lists_what_goes():
    gmail = TidyingGmail()
    ctx = FakeContext(FakeResult("accept", TrashApproval(confirm=True)))
    result = await trash_guarded(gmail, ctx, ["m1", "m2"])
    assert result == {"trashed": 2}
    assert gmail.trashed == ["m1", "m2"]
    assert "Subject m1" in ctx.messages[0]
    assert "Subject m2" in ctx.messages[0]


@pytest.mark.parametrize(
    "answer",
    [
        FakeResult("decline"),
        FakeResult("cancel"),
        FakeResult("accept", None),
        FakeResult("accept", TrashApproval(confirm=False)),
    ],
    ids=["decline", "cancel", "no data", "confirm false"],
)
async def test_trash_without_a_yes_moves_nothing(answer):
    gmail = TidyingGmail()
    result = await trash_guarded(gmail, FakeContext(answer), ["m1"])
    assert result["trashed"] == 0
    assert gmail.trashed == []


async def test_trash_when_nobody_can_be_asked_moves_nothing():
    class Broken:
        async def elicit(self, message, schema):
            raise RuntimeError("client does not support elicitation")

    gmail = TidyingGmail()
    result = await trash_guarded(gmail, Broken(), ["m1"])
    assert gmail.trashed == []
    assert "could not be confirmed" in result["reason"]


async def test_trash_of_only_skipped_ids_asks_nothing():
    gmail = TidyingGmail(skip={"m9"})
    ctx = FakeContext(FakeResult("accept", TrashApproval(confirm=True)))
    result = await trash_guarded(gmail, ctx, ["m9"])
    assert result == {"trashed": 0, "skipped": 1}
    assert ctx.messages == []


async def test_trash_question_stays_readable_for_a_big_batch():
    gmail = TidyingGmail()
    ctx = FakeContext(FakeResult("decline"))
    await trash_guarded(gmail, ctx, [f"m{i}" for i in range(40)])
    question = ctx.messages[0]
    assert "40" in question
    assert "Subject m39" not in question
    assert question.count("\n") < 35


async def test_trash_without_write_access_returns_the_reason():
    gmail = TidyingGmail(write_error=WriteNotAuthorised("re-run the Google login"))
    ctx = FakeContext(FakeResult("accept", TrashApproval(confirm=True)))
    result = await trash_guarded(gmail, ctx, ["m1"])
    assert result == {"trashed": 0, "reason": "re-run the Google login"}


# ── attachments ──────────────────────────────────────────


class ReadingGmail:
    def __init__(self, files):
        self.files = files

    def metadata(self, message_id):
        return MessageMeta(id=message_id, thread_id="t1", sender="a", subject="s", date="d", list_id="")

    def content(self, message_id):
        return "text", self.files


def test_a_message_lists_its_files_only_when_it_has_some():
    # No key for plain mail: the cron passes read a lot of it.
    assert "attachments" not in read_message_guarded(ReadingGmail([]), "m1")
    files = [{"part_id": "1", "filename": "a.pdf", "mime_type": "application/pdf", "size": 3}]
    assert read_message_guarded(ReadingGmail(files), "m1")["attachments"] == files


class AttachingGmail:
    def __init__(self, refuse=None):
        self.refuse = refuse
        self.calls = []

    def save_attachment(self, message_id, part_id):
        if self.refuse:
            raise self.refuse
        return {"path": "/shared/mail/m1/a.pdf", "filename": "a.pdf", "mime_type": "application/pdf", "size": 3}

    def create_draft(self, to, subject, body, attachments=()):
        if self.refuse:
            raise self.refuse
        self.calls.append(list(attachments))
        return "d3"

    def create_reply_draft(self, message_id, body, attachments=()):
        return self.create_draft("", "", body, attachments)


def test_saving_returns_the_path():
    assert save_attachment_guarded(AttachingGmail(), "m1", "1")["path"] == "/shared/mail/m1/a.pdf"


@pytest.mark.parametrize(
    "refusal, expected",
    [
        (ExcludedMessage("denied domain"), {"excluded": True, "reason": "denied domain"}),
        (AttachmentRefused("no part"), {"saved": False, "reason": "no part"}),
        (OSError("disk full"), {"saved": False, "reason": "saving failed (OSError)"}),
    ],
)
def test_saving_refusals_are_results(refusal, expected):
    assert save_attachment_guarded(AttachingGmail(refuse=refusal), "m1", "1") == expected


def test_a_draft_passes_its_files_on():
    gmail = AttachingGmail()
    assert create_draft_guarded(gmail, "a@b.c", "s", "t", ["/shared/outbox/x.pdf"]) == {"draft_id": "d3"}
    assert gmail.calls == [["/shared/outbox/x.pdf"]]


def test_a_refused_file_is_a_result_not_an_exception():
    gmail = AttachingGmail(refuse=AttachmentRefused("x: no such file"))
    assert create_draft_guarded(gmail, "a@b.c", "s", "t", ["x"]) == {"draft_id": None, "reason": "x: no such file"}
    assert create_reply_draft_guarded(gmail, "m1", "t", ["x"]) == {"draft_id": None, "reason": "x: no such file"}


async def test_the_question_lists_the_attachments():
    class WithFiles(FakeGmail):
        def draft(self, draft_id):
            return {**super().draft(draft_id), "attachments": [{"filename": "док.pdf", "size": 184213}]}

    ctx = FakeContext(FakeResult("decline"))
    await send_draft_guarded(WithFiles(), ctx, "d1")
    assert "📎 док.pdf (180 KB)" in ctx.messages[0]


@pytest.mark.parametrize("size, text", [(900, "900 B"), (184213, "180 KB"), (3 * 1048576, "3.0 MB")])
def test_human_size(size, text):
    assert human_size(size) == text


def test_a_thread_names_the_message_of_each_file(monkeypatch):
    # gmail_save_attachment needs the message id; a thread entry without one
    # would leave hermes a file it cannot fetch.
    from gmail_mcp import server

    files = [{"part_id": "1", "filename": "a.pdf", "mime_type": "application/pdf", "size": 3}]
    meta = MessageMeta(id="m2", thread_id="t1", sender="a", subject="s", date="d", list_id="")

    class Threading:
        def thread(self, thread_id):
            return [(meta, "plain", []), (meta, "with file", files)]

    monkeypatch.setattr(server, "_gmail", Threading())
    plain, with_file = server.gmail_read_thread("t1")
    assert "attachments" not in plain and "message_id" not in plain
    assert with_file["message_id"] == "m2" and with_file["attachments"] == files


# ── changing drafts ──────────────────────────────────────


class DraftingGmail:
    def __init__(self, error=None):
        self.error = error
        self.updated = []
        self.deleted = []

    def drafts(self, query="", limit=10):
        if self.error:
            raise self.error
        return [{"draft_id": "r-1", "to": "a@b.c", "subject": "Re: x", "body": "Hi"}]

    def update_draft(self, draft_id, body, attachments=None):
        if self.error:
            raise self.error
        self.updated.append((draft_id, body, attachments))
        return draft_id

    def draft(self, draft_id):
        if draft_id == "missing":
            raise KeyError(draft_id)
        return {
            "to": "support@acme.example",
            "subject": "Re: acme case",
            "body": "Hi Joev",
            "attachments": [{"filename": "a.pdf", "size": 30607}],
        }

    def delete_draft(self, draft_id):
        self.deleted.append(draft_id)


def test_listing_drafts_returns_them():
    assert list_drafts_guarded(DraftingGmail(), "acme", 10)[0]["draft_id"] == "r-1"


def test_a_listing_failure_is_a_result_not_an_exception():
    result = list_drafts_guarded(DraftingGmail(error=RuntimeError("boom")), "", 10)
    assert result == {"drafts": [], "reason": "listing drafts failed (RuntimeError)"}


def test_updating_a_draft_keeps_its_id():
    gmail = DraftingGmail()
    assert update_draft_guarded(gmail, "r-1", "new", None) == {"draft_id": "r-1"}
    assert gmail.updated == [("r-1", "new", None)]


def test_updating_an_excluded_draft_returns_a_marker():
    result = update_draft_guarded(DraftingGmail(error=ExcludedMessage("skip label")), "r-1", "x", None)
    assert result == {"excluded": True, "reason": "skip label"}


def test_a_refused_file_on_update_says_why():
    result = update_draft_guarded(DraftingGmail(error=AttachmentRefused("no such file")), "r-1", "x", ["f"])
    assert result == {"draft_id": None, "reason": "no such file"}


def test_any_other_update_failure_is_a_result():
    result = update_draft_guarded(DraftingGmail(error=KeyError("r-9")), "r-9", "x", None)
    assert result == {"draft_id": None, "reason": "updating the draft failed (KeyError)"}


async def test_deleting_asks_first_and_shows_the_draft():
    gmail = DraftingGmail()
    ctx = FakeContext(FakeResult("accept", DeleteApproval(confirm=True)))
    assert await delete_draft_guarded(gmail, ctx, "r-1") == {"deleted": True}
    assert gmail.deleted == ["r-1"]
    assert "support@acme.example" in ctx.messages[0]
    assert "Hi Joev" in ctx.messages[0]
    assert "a.pdf" in ctx.messages[0]


@pytest.mark.parametrize(
    "answer",
    [FakeResult("decline"), FakeResult("cancel"), FakeResult("accept", None),
     FakeResult("accept", DeleteApproval(confirm=False))],
    ids=["decline", "cancel", "no data", "confirm false"],
)
async def test_deleting_without_a_yes_deletes_nothing(answer):
    gmail = DraftingGmail()
    result = await delete_draft_guarded(gmail, FakeContext(answer), "r-1")
    assert result["deleted"] is False
    assert gmail.deleted == []


async def test_deleting_a_missing_draft_asks_nothing():
    gmail = DraftingGmail()
    ctx = FakeContext(FakeResult("accept", DeleteApproval(confirm=True)))
    result = await delete_draft_guarded(gmail, ctx, "missing")
    assert result == {"deleted": False, "reason": "draft not found (KeyError)"}
    assert ctx.messages == []
