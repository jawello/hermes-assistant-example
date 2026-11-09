import base64
import mimetypes

import pytest

from gmail_mcp.exclusions import Exclusions
from gmail_mcp.gmail import (
    AttachmentRefused,
    ExcludedMessage,
    GmailClient,
    MessageMeta,
    WriteNotAuthorised,
    safe_name,
)


class FakeHttpError(Exception):
    """Stands in for googleapiclient.errors.HttpError."""

    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.resp = type("Resp", (), {"status": status})()


class Call:
    """One pending API call: googleapiclient returns a request object whose
    execute() performs it."""

    def __init__(self, result):
        self._result = result

    def execute(self):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeMessages:
    def __init__(self, listings, messages, attachment_data=None):
        self._listings = listings
        self._messages = messages
        self._attachment_data = attachment_data or {}
        self.list_queries = []

    def list(self, userId, **kwargs):
        self.list_queries.append(kwargs)
        return Call(self._listings.pop(0))

    def get(self, userId, id, **kwargs):
        if id not in self._messages:
            return Call(FakeHttpError(404))
        return Call(self._messages[id])

    def attachments(self):
        data = self._attachment_data

        class A:
            def get(self, userId, messageId, id):
                return Call({"data": data[id], "size": len(data[id])})

        return A()

    # Writes: recorded, and failed with `write_error` when a test sets one —
    # a refresh token granted without gmail.modify answers 403 here.
    write_error = None
    batch_modified = None
    trashed = None

    def batchModify(self, userId, body):
        self.batch_modified = (self.batch_modified or []) + [body]
        return Call(self.write_error or {})

    def trash(self, userId, id):
        self.trashed = (self.trashed or []) + [id]
        return Call(self.write_error or {"id": id})


class FakeThreads:
    def __init__(self, threads):
        self._threads = threads

    def get(self, userId, id, **kwargs):
        return Call(self._threads[id])


class FakeDrafts:
    """Records what drafts().create() was handed, so a test can assert on the
    raw MIME and the threadId Gmail needs to file a reply in its thread."""

    def __init__(self):
        self.created = []
        self.media = []
        self.stored = {}
        self.listing = {}
        self.list_queries = []
        self.updated = []
        self.deleted = []

    def create(self, userId, body, media_body=None):
        self.created.append(body)
        self.media.append(media_body)
        return Call({"id": f"d{len(self.created)}"})

    def get(self, userId, id, format):
        return Call(self.stored[id])

    def list(self, userId, **kwargs):
        self.list_queries.append(kwargs)
        return Call(self.listing)

    def update(self, userId, id, body, media_body=None):
        self.updated.append((id, body, media_body))
        return Call({"id": id})

    def delete(self, userId, id):
        self.deleted.append(id)
        return Call("")


class FakeUsers:
    def __init__(self, messages=None, profile=None, labels=None, threads=None):
        self._messages = messages
        self._profile = profile or {"emailAddress": "owner@example.com"}
        self._labels = labels or {"labels": []}
        self._threads = FakeThreads(threads or {})
        self._drafts = FakeDrafts()
        self.label_list_calls = 0

    def messages(self):
        return self._messages

    def threads(self):
        return self._threads

    def drafts(self):
        return self._drafts

    def getProfile(self, userId):
        return Call(self._profile)

    def labels(self):
        outer = self

        class L:
            def list(self, userId):
                outer.label_list_calls += 1
                return Call(outer._labels)

        return L()


class FakeService:
    def __init__(self, users):
        self._users = users

    def users(self):
        return self._users


def b64(text):
    return base64.urlsafe_b64encode(text.encode()).decode()


MESSAGE = {
    "id": "m1",
    "threadId": "t1",
    "snippet": "the snippet",
    "payload": {
        "headers": [
            {"name": "From", "value": "Bank <no-reply@bank.example>"},
            {"name": "Subject", "value": "Statement ready"},
            {"name": "Date", "value": "Mon, 11 Aug 2026 09:00:00 +0000"},
            {"name": "List-Id", "value": "<news.bank.example>"},
        ],
        "mimeType": "text/plain",
        "body": {"data": b64("Your statement is ready")},
    },
    "labelIds": ["INBOX", "UNREAD"],
}


def client_with(users):
    return GmailClient(FakeService(users), http_error=FakeHttpError)


def test_metadata_reads_the_headers_it_needs():
    users = FakeUsers(messages=FakeMessages([], {"m1": MESSAGE}))
    meta = client_with(users).metadata("m1")
    assert meta == MessageMeta(
        id="m1",
        thread_id="t1",
        sender="Bank <no-reply@bank.example>",
        subject="Statement ready",
        date="Mon, 11 Aug 2026 09:00:00 +0000",
        list_id="<news.bank.example>",
        label_ids=["INBOX", "UNREAD"],
        snippet="the snippet",
    )


def test_metadata_tolerates_a_message_with_no_subject():
    bare = {"id": "m2", "threadId": "t2", "payload": {"headers": []}}
    users = FakeUsers(messages=FakeMessages([], {"m2": bare}))
    meta = client_with(users).metadata("m2")
    assert meta.subject == ""
    assert meta.sender == ""
    assert meta.label_ids == []


def test_headers_are_matched_case_insensitively():
    # RFC 5322 header names are case-insensitive and senders vary: "Message-ID",
    # "list-id", "SUBJECT" all occur in real mail.
    odd = {
        "id": "m3",
        "threadId": "t3",
        "payload": {"headers": [{"name": "subject", "value": "lowercase"}]},
    }
    users = FakeUsers(messages=FakeMessages([], {"m3": odd}))
    assert client_with(users).metadata("m3").subject == "lowercase"


def test_body_goes_through_the_mime_extractor():
    users = FakeUsers(messages=FakeMessages([], {"m1": MESSAGE}))
    assert client_with(users).body("m1") == "Your statement is ready"


CLINIC = {
    "id": "m9",
    "threadId": "t9",
    "snippet": "appointment",
    "payload": {
        "headers": [
            {"name": "From", "value": "Doctor <doc@clinic.example>"},
            {"name": "Subject", "value": "Your results"},
            {"name": "Date", "value": "Mon, 11 Aug 2026 09:00:00 +0000"},
        ],
        "mimeType": "text/plain",
        "body": {"data": b64("Private text")},
    },
    "labelIds": ["INBOX"],
}

RULES = Exclusions(senders=[], domains=["clinic.example"])


def excluding_client(users):
    return GmailClient(FakeService(users), http_error=FakeHttpError, rules=RULES)


def test_search_omits_an_excluded_message():
    users = FakeUsers(
        messages=FakeMessages(
            [{"messages": [{"id": "m1"}, {"id": "m9"}]}], {"m1": MESSAGE, "m9": CLINIC}
        ),
    )
    results = excluding_client(users).search("newer_than:1d")
    assert [m.id for m in results] == ["m1"]


def test_metadata_refuses_an_excluded_message():
    users = FakeUsers(messages=FakeMessages([], {"m9": CLINIC}))
    with pytest.raises(ExcludedMessage):
        excluding_client(users).metadata("m9")


def test_body_refuses_an_excluded_message():
    users = FakeUsers(messages=FakeMessages([], {"m9": CLINIC}))
    with pytest.raises(ExcludedMessage):
        excluding_client(users).body("m9")


def test_thread_omits_an_excluded_message():
    thread = {"messages": [MESSAGE, CLINIC]}
    users = FakeUsers(messages=FakeMessages([], {}), threads={"t1": thread})
    out = excluding_client(users).thread("t1")
    assert [meta.id for meta, *_ in out] == ["m1"]


def test_the_skip_label_is_looked_up_once_per_client():
    # Two reads must not mean two labels().list() round-trips: the poller used
    # to fetch this once per cycle, and the client now has to cache it itself.
    users = FakeUsers(
        messages=FakeMessages([], {"m1": MESSAGE}),
        labels={"labels": [{"id": "Label_7", "name": "assistant/skip"}]},
    )
    client = excluding_client(users)
    client.metadata("m1")
    client.metadata("m1")
    assert users.label_list_calls == 1


MINE = {
    "id": "m2",
    "threadId": "t1",
    "snippet": "my reply",
    "payload": {
        "headers": [
            {"name": "From", "value": "the user <owner@example.com>"},
            {"name": "Subject", "value": "Re: Statement ready"},
            {"name": "Date", "value": "Mon, 11 Aug 2026 10:00:00 +0000"},
        ],
        "mimeType": "text/plain",
        "body": {"data": b64("Already paid it")},
    },
    "labelIds": ["SENT"],
}


def test_a_thread_keeps_his_own_replies():
    # The "self" rule exists so his outgoing mail is not treated as something
    # new to react to. Inside a thread it would delete his half of the
    # conversation, which is usually the half that says what was decided.
    users = FakeUsers(messages=FakeMessages([], {}), threads={"t1": {"messages": [MESSAGE, MINE]}})
    out = excluding_client(users).thread("t1")
    assert [meta.id for meta, *_ in out] == ["m1", "m2"]


def test_a_thread_still_drops_an_excluded_sender():
    # Relaxing "self" must not relax the privacy rules.
    users = FakeUsers(
        messages=FakeMessages([], {}),
        threads={"t1": {"messages": [MESSAGE, MINE, CLINIC]}},
    )
    out = excluding_client(users).thread("t1")
    assert [meta.id for meta, *_ in out] == ["m1", "m2"]


def test_search_still_omits_his_own_mail():
    users = FakeUsers(
        messages=FakeMessages([{"messages": [{"id": "m1"}, {"id": "m2"}]}], {"m1": MESSAGE, "m2": MINE}),
    )
    assert [m.id for m in excluding_client(users).search("newer_than:1d")] == ["m1"]


# ── reply drafts ─────────────────────────────────────────
# A draft written with no threadId and no In-Reply-To/References is a new
# conversation, whatever the subject says. On 2026-09-21 an answer to a sender
# landed outside his thread for exactly that reason.

shop = {
    "id": "m20",
    "threadId": "t20",
    "payload": {
        "headers": [
            {"name": "From", "value": "Dr Andri <info@exampleshop.example>"},
            {"name": "Subject", "value": "JESSIE and BUBLIK"},
            {"name": "Date", "value": "Sun, 21 Sep 2026 08:00:00 +0000"},
            {"name": "Message-ID", "value": "<shop-1@exampleshop.example>"},
        ],
    },
    "labelIds": ["INBOX"],
}


def parsed_draft(users):
    """The single draft the fake recorded, as (body_dict, mime_message)."""
    import email
    import email.policy

    body = users.drafts().created[-1]
    media = users.drafts().media[-1]
    if media is not None:
        raw = media.getbytes(0, media.size())
    else:
        raw = base64.urlsafe_b64decode(body["message"]["raw"].encode())
    return body, email.message_from_bytes(raw, policy=email.policy.default)


def test_a_reply_draft_goes_into_the_original_thread():
    users = FakeUsers(messages=FakeMessages([], {"m20": shop}))
    client_with(users).create_reply_draft("m20", "Jessie is much better")
    body, _ = parsed_draft(users)
    assert body["message"]["threadId"] == "t20"


def test_a_reply_draft_carries_the_threading_headers():
    users = FakeUsers(messages=FakeMessages([], {"m20": shop}))
    client_with(users).create_reply_draft("m20", "Jessie is much better")
    _, mime = parsed_draft(users)
    assert mime["In-Reply-To"] == "<shop-1@exampleshop.example>"
    assert mime["References"] == "<shop-1@exampleshop.example>"


def test_a_reply_draft_extends_an_existing_references_chain():
    # RFC 5322: References is the parent's chain plus the parent's Message-ID.
    # Replacing it instead of extending it orphans the reply in long threads.
    deep = dict(shop, payload={"headers": shop["payload"]["headers"] + [
        {"name": "References", "value": "<shop-0@exampleshop.example>"},
    ]})
    users = FakeUsers(messages=FakeMessages([], {"m20": deep}))
    client_with(users).create_reply_draft("m20", "thanks")
    _, mime = parsed_draft(users)
    assert mime["References"] == "<shop-0@exampleshop.example> <shop-1@exampleshop.example>"


def test_a_reply_draft_prefixes_the_subject_with_re():
    users = FakeUsers(messages=FakeMessages([], {"m20": shop}))
    client_with(users).create_reply_draft("m20", "thanks")
    _, mime = parsed_draft(users)
    assert mime["Subject"] == "Re: JESSIE and BUBLIK"


def test_a_reply_draft_does_not_stack_re_prefixes():
    already = dict(shop, payload={"headers": [
        h for h in shop["payload"]["headers"] if h["name"] != "Subject"
    ] + [{"name": "Subject", "value": "Re: JESSIE and BUBLIK"}]})
    users = FakeUsers(messages=FakeMessages([], {"m20": already}))
    client_with(users).create_reply_draft("m20", "thanks")
    _, mime = parsed_draft(users)
    assert mime["Subject"] == "Re: JESSIE and BUBLIK"


def test_a_reply_draft_answers_the_reply_to_address_when_there_is_one():
    # Mailing lists and ticketing systems send From: a no-reply address and
    # Reply-To: the one that actually reaches a human.
    routed = dict(shop, payload={"headers": shop["payload"]["headers"] + [
        {"name": "Reply-To", "value": "desk@exampleshop.example"},
    ]})
    users = FakeUsers(messages=FakeMessages([], {"m20": routed}))
    client_with(users).create_reply_draft("m20", "thanks")
    _, mime = parsed_draft(users)
    assert mime["To"] == "desk@exampleshop.example"


def test_a_reply_draft_answers_the_sender_when_there_is_no_reply_to():
    users = FakeUsers(messages=FakeMessages([], {"m20": shop}))
    client_with(users).create_reply_draft("m20", "thanks")
    _, mime = parsed_draft(users)
    assert mime["To"] == "Dr Andri <info@exampleshop.example>"


def test_a_reply_draft_refuses_an_excluded_message():
    # The privacy rules gate writing as well as reading: a draft to an
    # excluded sender quotes its thread back into Gmail's send path.
    users = FakeUsers(messages=FakeMessages([], {"m9": CLINIC}))
    with pytest.raises(ExcludedMessage):
        excluding_client(users).create_reply_draft("m9", "thanks")
    assert users.drafts().created == []


def test_a_reply_draft_can_follow_his_own_last_message():
    # The "self" rule keeps his outgoing mail from looking like something new
    # to react to. Replying under his own last message is a normal thing to
    # want, so it must not trip that rule.
    users = FakeUsers(messages=FakeMessages([], {"m2": MINE}))
    excluding_client(users).create_reply_draft("m2", "one more thing")
    body, _ = parsed_draft(users)
    assert body["message"]["threadId"] == "t1"


def test_a_plain_draft_starts_its_own_thread():
    users = FakeUsers(messages=FakeMessages([], {}))
    client_with(users).create_draft("someone@example.com", "Hello", "text")
    body, mime = parsed_draft(users)
    assert "threadId" not in body["message"]
    assert mime["In-Reply-To"] is None


# ── tidying: archive and trash ───────────────────────────


def tidying_client(messages):
    users = FakeUsers(messages=messages)
    return excluding_client(users), messages


def test_tidy_targets_drop_excluded_and_unknown_ids():
    # The privacy rules gate writing as well as reading, and an id that does
    # not exist must not fail the other ids in the same call.
    client, _ = tidying_client(FakeMessages([], {"m1": MESSAGE, "m9": CLINIC}))
    targets, skipped = client.tidy_targets(["m1", "m9", "gone"])
    assert [m.id for m in targets] == ["m1"]
    assert skipped == 2


def test_archive_removes_only_the_inbox_label_in_one_call():
    client, messages = tidying_client(FakeMessages([], {}))
    client.archive(["m1", "m2"])
    assert messages.batch_modified == [{"ids": ["m1", "m2"], "removeLabelIds": ["INBOX"]}]


def test_trash_moves_each_message_to_trash_rather_than_deleting():
    client, messages = tidying_client(FakeMessages([], {}))
    client.trash(["m1", "m2"])
    assert messages.trashed == ["m1", "m2"]


@pytest.mark.parametrize("action", ["archive", "trash"])
def test_a_token_without_write_access_says_so(action):
    # Until the user re-runs the Google login with gmail.modify, every write
    # answers 403. That must read as "not authorised yet", not as a crash.
    messages = FakeMessages([], {})
    messages.write_error = FakeHttpError(403)
    client, _ = tidying_client(messages)
    with pytest.raises(WriteNotAuthorised):
        getattr(client, action)(["m1"])


def test_other_write_errors_are_not_disguised_as_missing_access():
    messages = FakeMessages([], {})
    messages.write_error = FakeHttpError(500)
    client, _ = tidying_client(messages)
    with pytest.raises(FakeHttpError):
        client.archive(["m1"])


# ── attachments: reading and saving ──────────────────────

REPORT = {
    "id": "m30",
    "threadId": "t30",
    "payload": {
        "partId": "",
        "headers": [
            {"name": "From", "value": "Bank <no-reply@bank.example>"},
            {"name": "Subject", "value": "Statement"},
        ],
        "mimeType": "multipart/mixed",
        "parts": [
            {"partId": "0", "mimeType": "text/plain", "body": {"data": b64("see attached")}},
            {"partId": "1", "mimeType": "application/pdf", "filename": "../../etc/passwd", "body": {"attachmentId": "A1", "size": 3}},
            {"partId": "2", "mimeType": "image/png", "filename": "image001.png", "body": {"attachmentId": "A2", "size": 3}},
            {"partId": "3", "mimeType": "image/png", "filename": "image001.png", "body": {"data": b64("png"), "size": 3}},
        ],
    },
    "labelIds": ["INBOX"],
}


def saving_client(tmp_path, messages):
    return GmailClient(
        FakeService(FakeUsers(messages=messages)), http_error=FakeHttpError, rules=RULES, shared_dir=tmp_path
    )


def test_content_returns_text_and_attachments_in_one_read():
    users = FakeUsers(messages=FakeMessages([], {"m30": REPORT}))
    text, attachments = client_with(users).content("m30")
    assert text == "see attached"
    assert [a["part_id"] for a in attachments] == ["1", "2", "3"]


def test_a_thread_carries_each_message_attachments():
    users = FakeUsers(messages=FakeMessages([], {}), threads={"t30": {"messages": [REPORT, MESSAGE]}})
    out = client_with(users).thread("t30")
    assert [len(files) for _, _, files in out] == [3, 0]


def test_saving_writes_the_file_under_the_message_folder(tmp_path):
    messages = FakeMessages([], {"m30": REPORT}, attachment_data={"A1": b64("pdf")})
    saved = saving_client(tmp_path, messages).save_attachment("m30", "1")
    path = tmp_path / "mail" / "m30" / saved["filename"]
    assert saved["path"] == str(path)
    assert path.read_bytes() == b"pdf"
    assert saved["size"] == 3 and saved["mime_type"] == "application/pdf"
    # The sender chose that name; it must not climb out of the folder.
    assert path.parent == tmp_path / "mail" / "m30"


def test_saving_inline_data_needs_no_second_request(tmp_path):
    messages = FakeMessages([], {"m30": REPORT})
    saved = saving_client(tmp_path, messages).save_attachment("m30", "3")
    assert (tmp_path / "mail" / "m30" / saved["filename"]).read_bytes() == b"png"


def test_two_attachments_with_one_name_do_not_overwrite_each_other(tmp_path):
    messages = FakeMessages([], {"m30": REPORT}, attachment_data={"A2": b64("one")})
    client = saving_client(tmp_path, messages)
    first, second = client.save_attachment("m30", "2"), client.save_attachment("m30", "3")
    assert first["path"] != second["path"]


def test_saving_refuses_an_unknown_part(tmp_path):
    with pytest.raises(AttachmentRefused):
        saving_client(tmp_path, FakeMessages([], {"m30": REPORT})).save_attachment("m30", "0")


def test_saving_refuses_an_excluded_message(tmp_path):
    with pytest.raises(ExcludedMessage):
        saving_client(tmp_path, FakeMessages([], {"m9": CLINIC})).save_attachment("m9", "1")
    assert not (tmp_path / "mail").exists()


@pytest.mark.parametrize(
    "name, expected",
    [
        ("../../etc/passwd", "_.._etc_passwd"),
        ("..", "attachment"),
        ("", "attachment"),
        ("a\x00b\nc.pdf", "abc.pdf"),
        (".hidden", "hidden"),
        ("док.pdf", "док.pdf"),
    ],
)
def test_safe_name(name, expected):
    assert safe_name(name) == expected


def test_safe_name_keeps_the_extension_when_cutting():
    cut = safe_name("я" * 300 + ".pdf")
    assert cut.endswith(".pdf") and len(cut.encode()) <= 200


# ── attachments: drafts ──────────────────────────────────


def outbox(tmp_path, name, data=b"%PDF-1.4"):
    folder = tmp_path / "outbox"
    folder.mkdir(exist_ok=True)
    (folder / name).write_bytes(data)
    return str(folder / name)


def drafting_client(tmp_path, users):
    return GmailClient(FakeService(users), http_error=FakeHttpError, shared_dir=tmp_path)


def test_a_draft_carries_its_attachments(tmp_path):
    users = FakeUsers(messages=FakeMessages([], {}))
    pdf = outbox(tmp_path, "док.pdf")
    outbox(tmp_path, "table.csv", b"a,b\n1,2\n")
    drafting_client(tmp_path, users).create_draft("me@example.com", "Files", "see", [pdf, "outbox/table.csv"])
    _, mime = parsed_draft(users)
    files = {p.get_filename(): p for p in mime.iter_attachments()}
    assert set(files) == {"док.pdf", "table.csv"}
    assert files["док.pdf"].get_content_type() == "application/pdf"
    assert files["table.csv"].get_payload(decode=True) == b"a,b\n1,2\n"
    assert users.drafts().media[-1] is not None  # uploaded, not inlined in JSON


@pytest.fixture
def slim_image_mimetypes(monkeypatch):
    """Python's types without a system /etc/mime.types, as in the image."""
    monkeypatch.setattr(mimetypes, "knownfiles", [])
    mimetypes.init()
    yield
    monkeypatch.undo()
    mimetypes.init()


def test_a_book_goes_with_its_own_type(tmp_path, slim_image_mimetypes):
    # The slim image has no /etc/mime.types, so Python alone sends an epub as
    # application/octet-stream, and Send-to-PocketBook dropped such a mail
    # silently (2026-10-14).
    users = FakeUsers(messages=FakeMessages([], {}))
    books = [outbox(tmp_path, name, b"PK\x03\x04") for name in ("b.epub", "b.fb2", "b.fb2.zip")]
    drafting_client(tmp_path, users).create_draft("me@example.com", "Book", "see", books)
    _, mime = parsed_draft(users)
    types = {p.get_filename(): p.get_content_type() for p in mime.iter_attachments()}
    assert types == {
        "b.epub": "application/epub+zip",
        "b.fb2": "application/x-fictionbook+xml",
        "b.fb2.zip": "application/zip",
    }


def test_a_plain_draft_is_not_uploaded(tmp_path):
    users = FakeUsers(messages=FakeMessages([], {}))
    drafting_client(tmp_path, users).create_draft("me@example.com", "Hi", "text")
    assert users.drafts().media[-1] is None


def test_a_reply_draft_with_files_stays_in_its_thread(tmp_path):
    users = FakeUsers(messages=FakeMessages([], {"m20": shop}))
    drafting_client(tmp_path, users).create_reply_draft("m20", "results attached", [outbox(tmp_path, "r.pdf")])
    body, mime = parsed_draft(users)
    assert body["message"]["threadId"] == shop["threadId"]
    assert mime["In-Reply-To"]
    assert [p.get_filename() for p in mime.iter_attachments()] == ["r.pdf"]


@pytest.mark.parametrize("bad", ["/etc/passwd", "outbox/../../x", "outbox/missing.pdf", "outbox"])
def test_files_outside_the_shared_folder_or_missing_are_refused(tmp_path, bad):
    outbox(tmp_path, "ok.pdf")
    users = FakeUsers(messages=FakeMessages([], {}))
    with pytest.raises(AttachmentRefused):
        drafting_client(tmp_path, users).create_draft("me@example.com", "x", "y", [bad])
    assert users.drafts().created == []


def test_a_symlink_out_of_the_shared_folder_is_refused(tmp_path):
    shared = tmp_path / "shared"
    (shared / "outbox").mkdir(parents=True)
    secret = tmp_path / "secret.txt"
    secret.write_text("no")
    (shared / "outbox" / "link.txt").symlink_to(secret)
    with pytest.raises(AttachmentRefused):
        drafting_client(shared, FakeUsers(messages=FakeMessages([], {}))).create_draft(
            "me@example.com", "x", "y", ["outbox/link.txt"]
        )


def test_more_than_25_mb_is_refused_with_the_drive_hint(tmp_path, monkeypatch):
    monkeypatch.setattr("gmail_mcp.gmail.MAX_ATTACHMENT_BYTES", 10)
    path = outbox(tmp_path, "big.bin", b"x" * 11)
    with pytest.raises(AttachmentRefused, match="Drive"):
        drafting_client(tmp_path, FakeUsers(messages=FakeMessages([], {}))).create_draft("a@b.c", "x", "y", [path])


def test_draft_lists_its_attachments():
    users = FakeUsers(messages=FakeMessages([], {}))
    users.drafts().stored["d9"] = {
        "message": {
            "payload": {
                "headers": [{"name": "To", "value": "a@b.c"}, {"name": "Subject", "value": "Files"}],
                "mimeType": "multipart/mixed",
                "parts": [
                    {"partId": "0", "mimeType": "text/plain", "body": {"data": b64("see")}},
                    {
                        "partId": "1",
                        "mimeType": "application/pdf",
                        "filename": "док.pdf",
                        "body": {"attachmentId": "A", "size": 184213},
                    },
                ],
            }
        }
    }
    draft = client_with(users).draft("d9")
    assert [(a["filename"], a["size"]) for a in draft["attachments"]] == [("док.pdf", 184213)]


# ── changing drafts ──────────────────────────────────────
# On 2026-10-15 a "fix the draft" request found no tool to list, change or
# delete one: the bot wrote a second draft beside the first and finished the
# job with five python -c scripts through another Google token.


def stored_draft(users, draft_id, mime, thread_id="t20", label_ids=("DRAFT",)):
    """Put a raw MIME draft where drafts().get() finds it, in both formats."""
    import email
    import email.policy

    raw = mime.as_bytes()
    parsed = email.message_from_bytes(raw, policy=email.policy.default)
    headers = [{"name": k, "value": str(v)} for k, v in parsed.items()]
    users.drafts().stored[draft_id] = {
        "id": draft_id,
        "message": {
            "id": f"msg-{draft_id}",
            "threadId": thread_id,
            "labelIds": list(label_ids),
            "raw": base64.urlsafe_b64encode(raw).decode(),
            "payload": {
                "headers": headers,
                "mimeType": "text/plain",
                "body": {"data": b64(parsed.get_body(("plain",)).get_content())},
            },
        },
    }


def reply_mime(body="old text", files=()):
    from email.message import EmailMessage

    m = EmailMessage()
    m["To"] = "acme Support <support@acme.example>"
    m["Subject"] = "Re: acme Support Case 1000"
    m["In-Reply-To"] = "<case-2@sfdc.example>"
    m["References"] = "<case-1@sfdc.example> <case-2@sfdc.example>"
    m.set_content(body)
    for name, data in files:
        m.add_attachment(data, maintype="application", subtype="pdf", filename=name)
    return m


def updated_draft(users):
    """The last drafts().update() call, as (draft_id, body_dict, mime_message)."""
    import email
    import email.policy

    draft_id, body, media = users.drafts().updated[-1]
    if media is not None:
        raw = media.getbytes(0, media.size())
    else:
        raw = base64.urlsafe_b64decode(body["message"]["raw"].encode())
    return draft_id, body, email.message_from_bytes(raw, policy=email.policy.default)


def test_drafts_are_listed_with_their_text_and_ids():
    users = FakeUsers(messages=FakeMessages([], {}))
    stored_draft(users, "r-1", reply_mime("Hi Joev"))
    users.drafts().listing = {"drafts": [{"id": "r-1"}]}
    [draft] = client_with(users).drafts("acme")
    assert users.drafts().list_queries[-1]["q"] == "acme"
    assert draft["draft_id"] == "r-1"
    assert draft["thread_id"] == "t20"
    assert draft["to"] == "acme Support <support@acme.example>"
    assert draft["subject"] == "Re: acme Support Case 1000"
    assert draft["body"].strip() == "Hi Joev"


def test_a_draft_under_the_skip_label_is_not_listed():
    users = FakeUsers(
        messages=FakeMessages([], {}),
        labels={"labels": [{"id": "L9", "name": "assistant/skip"}]},
    )
    stored_draft(users, "r-1", reply_mime(), label_ids=("DRAFT", "L9"))
    users.drafts().listing = {"drafts": [{"id": "r-1"}]}
    assert client_with(users).drafts() == []


def test_updating_a_draft_replaces_the_text_and_keeps_the_rest(tmp_path):
    users = FakeUsers(messages=FakeMessages([], {}))
    stored_draft(users, "r-1", reply_mime("old text", [("a.pdf", b"%PDF-a")]))
    drafting_client(tmp_path, users).update_draft("r-1", "new text")
    draft_id, body, mime = updated_draft(users)
    assert draft_id == "r-1"
    assert body["message"]["threadId"] == "t20"
    assert mime.get_body(("plain",)).get_content().strip() == "new text"
    assert mime["To"] == "acme Support <support@acme.example>"
    assert mime["Subject"] == "Re: acme Support Case 1000"
    assert mime["In-Reply-To"] == "<case-2@sfdc.example>"
    assert mime["References"] == "<case-1@sfdc.example> <case-2@sfdc.example>"
    files = [(p.get_filename(), p.get_payload(decode=True)) for p in mime.iter_attachments()]
    assert files == [("a.pdf", b"%PDF-a")]


def test_updating_with_a_file_list_replaces_the_files(tmp_path):
    users = FakeUsers(messages=FakeMessages([], {}))
    stored_draft(users, "r-1", reply_mime("old", [("a.pdf", b"%PDF-a")]))
    drafting_client(tmp_path, users).update_draft("r-1", "new", [outbox(tmp_path, "b.pdf", b"%PDF-b")])
    _, _, mime = updated_draft(users)
    assert [p.get_filename() for p in mime.iter_attachments()] == ["b.pdf"]


def test_updating_with_an_empty_file_list_drops_the_files(tmp_path):
    users = FakeUsers(messages=FakeMessages([], {}))
    stored_draft(users, "r-1", reply_mime("old", [("a.pdf", b"%PDF-a")]))
    drafting_client(tmp_path, users).update_draft("r-1", "new", [])
    _, _, mime = updated_draft(users)
    assert list(mime.iter_attachments()) == []


def test_a_refused_file_leaves_the_draft_unchanged(tmp_path):
    users = FakeUsers(messages=FakeMessages([], {}))
    stored_draft(users, "r-1", reply_mime("old"))
    with pytest.raises(AttachmentRefused):
        drafting_client(tmp_path, users).update_draft("r-1", "new", ["/etc/passwd"])
    assert users.drafts().updated == []


def test_a_draft_under_the_skip_label_is_not_updated(tmp_path):
    users = FakeUsers(
        messages=FakeMessages([], {}),
        labels={"labels": [{"id": "L9", "name": "assistant/skip"}]},
    )
    stored_draft(users, "r-1", reply_mime(), label_ids=("DRAFT", "L9"))
    with pytest.raises(ExcludedMessage):
        drafting_client(tmp_path, users).update_draft("r-1", "new")
    assert users.drafts().updated == []


def test_deleting_a_draft():
    users = FakeUsers(messages=FakeMessages([], {}))
    client_with(users).delete_draft("r-1")
    assert users.drafts().deleted == ["r-1"]
