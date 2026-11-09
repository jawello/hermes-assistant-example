"""The send gate's logic, against a fake Telethon client and a fake elicit.

Every write path must reach Telegram only after an explicit accept, and only
for a chat on the allowlist; everything else returns a reason and sends nothing.
"""
import asyncio
import json
import stat
from types import SimpleNamespace

import pytest

from send_gate import core

ME = 1


# Telethon's entity classes, by name: core classifies by class name so it
# needs no Telethon at test time.
class User(SimpleNamespace):
    pass


class Chat(SimpleNamespace):
    pass


class Channel(SimpleNamespace):
    pass


PETYA = User(id=42, first_name="Петя", last_name="Иванов", username="petya", bot=False)
MASHA = User(id=43, first_name="Маша", last_name=None, username=None, bot=False)
SELF = User(id=ME, first_name="the user", last_name=None, username="the user", bot=False)
SERVICE = User(id=777000, first_name="Telegram", last_name=None, username=None, bot=False)
BOT = User(id=50, first_name="Some", last_name="Bot", username="some_bot", bot=True)
FAMILY = Chat(id=7, title="Семья")
CLUB = Channel(id=8, title="Клуб", username="club", broadcast=False, megagroup=True)
NEWS = Channel(id=9, title="Новости", username="news", broadcast=True, megagroup=False)


class FakeClient:
    def __init__(self, messages=None):
        self.sent = []
        self.messages = messages or {}

    async def get_me(self):
        return SELF

    async def get_messages(self, entity, ids):
        return self.messages.get((core.peer_id(entity), ids))

    async def send_message(self, entity, text, parse_mode, reply_to=None):
        self.sent.append({"to": core.peer_id(entity), "text": text,
                          "parse_mode": parse_mode, "reply_to": reply_to})
        return SimpleNamespace(id=1000 + len(self.sent))

    async def send_file(self, entity, file, caption, parse_mode, reply_to=None, force_document=False):
        self.sent.append({"to": core.peer_id(entity), "file": file, "caption": caption,
                          "parse_mode": parse_mode, "reply_to": reply_to,
                          "force_document": force_document})
        if isinstance(file, list):
            return [SimpleNamespace(id=2000 + i) for i in range(len(file))]
        return SimpleNamespace(id=2000)


ALIASES = {"@petya": PETYA, "petya": PETYA, 42: PETYA, "Петя": PETYA, "42": PETYA,
           43: MASHA, "@masha": MASHA, 777000: SERVICE, ME: SELF, "me": SELF,
           50: BOT, -7: FAMILY, "Семья": FAMILY, -1000000000008: CLUB, "@club": CLUB,
           "@news": NEWS}


async def resolve(chat_id, client=None):
    try:
        return ALIASES[chat_id]
    except KeyError:
        raise ValueError(f"Cannot find any entity corresponding to {chat_id!r}")


class Elicit:
    """Records each question; answers with `action` (or raises `error`)."""

    def __init__(self, action="accept", confirm=True, error=None):
        self.action, self.confirm, self.error = action, confirm, error
        self.questions = []

    async def __call__(self, message, schema):
        self.questions.append(message)
        assert all(f.default is not None or not f.is_required()
                   for f in schema.model_fields.values())
        if self.error:
            raise self.error
        data = schema(confirm=self.confirm) if self.action == "accept" else None
        return SimpleNamespace(action=self.action, data=data)


@pytest.fixture
def allowlist(tmp_path):
    return core.Allowlist(tmp_path / "state" / "send-allowlist.json")


def run(coro):
    return asyncio.run(coro)


def allowed(allowlist, *entities):
    for e in entities:
        allowlist.add({"id": core.peer_id(e), "title": core.title(e), "type": "user",
                       "username": getattr(e, "username", None), "added_at": "2026-10-13T00:00:00Z"})


# --- classification -----------------------------------------------------

def test_peer_ids_follow_telethon():
    assert core.peer_id(PETYA) == 42
    assert core.peer_id(FAMILY) == -7
    assert core.peer_id(CLUB) == -1000000000008


@pytest.mark.parametrize("entity, expected", [
    (PETYA, "user"), (SELF, "self"), (SERVICE, "service"), (BOT, "bot"),
    (FAMILY, "group"), (CLUB, "supergroup"), (NEWS, "channel"),
])
def test_kinds(entity, expected):
    assert core.kind(entity, ME) == expected


# --- send ---------------------------------------------------------------

def test_accept_sends_plain_text_with_parse_mode_none(allowlist):
    allowed(allowlist, PETYA)
    client, elicit = FakeClient(), Elicit()
    out = run(core.send(client, resolve, elicit, allowlist, "@petya", "Да, буду к 12"))
    assert client.sent == [{"to": 42, "text": "Да, буду к 12", "parse_mode": None, "reply_to": None}]
    assert out == {"sent": True, "chat": "Петя Иванов", "chat_id": 42, "message_id": 1001}


@pytest.mark.parametrize("elicit", [
    Elicit(action="decline"), Elicit(action="cancel"), Elicit(confirm=False),
    Elicit(error=RuntimeError("no channel")), Elicit(error=asyncio.TimeoutError()),
])
def test_anything_but_accept_sends_nothing(allowlist, elicit):
    allowed(allowlist, PETYA)
    client = FakeClient()
    out = run(core.send(client, resolve, elicit, allowlist, "@petya", "привет"))
    assert client.sent == []
    assert out["sent"] is False and out["reason"]


def test_cancellation_while_waiting_sends_nothing(allowlist):
    # Upstream's 240 s wait_for cancels the whole call.
    allowed(allowlist, PETYA)
    client = FakeClient()

    async def hang(message, schema):
        await asyncio.sleep(3600)

    async def main():
        task = asyncio.create_task(core.send(client, resolve, hang, allowlist, "@petya", "x"))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(main())
    assert client.sent == []


def test_slow_answer_times_out(allowlist, monkeypatch):
    monkeypatch.setattr(core, "CONFIRM_TIMEOUT", 0.01)
    allowed(allowlist, PETYA)
    client = FakeClient()

    async def slow(message, schema):
        await asyncio.sleep(1)

    out = run(core.send(client, resolve, slow, allowlist, "@petya", "x"))
    assert client.sent == [] and "вовремя" in out["reason"]


def test_confirmation_shows_recipient_mode_source_and_reply(allowlist):
    allowed(allowlist, PETYA)
    reply = SimpleNamespace(id=5, message="Ты в субботу свободен? " + "очень длинно " * 30)
    client, elicit = FakeClient(messages={(42, 5): reply}), Elicit()
    run(core.send(client, resolve, elicit, allowlist, "@petya", "**Да**, [тут](https://x.example/a)",
                  parse_mode="md", reply_to_message_id=5))
    q = elicit.questions[0]
    assert "«Петя Иванов»" in q and "@petya" in q and "личка" in q and "markdown" in q
    assert "↩" in q and "Ты в субботу свободен?" in q and "…" in q
    assert q.endswith("**Да**, [тут](https://x.example/a)")
    assert client.sent[0]["parse_mode"] == "md" and client.sent[0]["reply_to"] == 5


def test_html_mode_is_passed_through(allowlist):
    allowed(allowlist, FAMILY)
    client = FakeClient()
    run(core.send(client, resolve, Elicit(), allowlist, "Семья", "<b>ужин</b>", parse_mode="html"))
    assert client.sent[0]["parse_mode"] == "html" and client.sent[0]["to"] == -7


def test_chat_off_the_list_is_refused_without_asking(allowlist):
    allowed(allowlist, PETYA)
    client, elicit = FakeClient(), Elicit()
    out = run(core.send(client, resolve, elicit, allowlist, "@masha", "привет"))
    assert elicit.questions == [] and client.sent == []
    assert out["sent"] is False and "allow_chat" in out["error"] and "Маша" in out["error"]


def test_allowlist_is_checked_against_the_resolved_id(allowlist):
    allowed(allowlist, PETYA)
    client = FakeClient()
    # Another name for the same person passes.
    out = run(core.send(client, resolve, Elicit(), allowlist, "Петя", "a"))
    assert out["sent"] is True and client.sent[0]["to"] == 42


@pytest.mark.parametrize("kwargs, expected", [
    ({"text": ""}, "пуст"),
    ({"text": "   "}, "пуст"),
    ({"text": "x" * 4097}, "4096"),
    ({"text": "x", "parse_mode": "rich_markdown"}, "parse_mode"),
    ({"text": "x", "parse_mode": "markdown"}, "parse_mode"),
    ({"text": "x", "reply_to_message_id": 99}, "99"),
])
def test_bad_input_is_refused_before_asking(allowlist, kwargs, expected):
    allowed(allowlist, PETYA)
    client, elicit = FakeClient(), Elicit()
    out = run(core.send(client, resolve, elicit, allowlist, "@petya", **kwargs))
    assert elicit.questions == [] and client.sent == []
    assert out["sent"] is False and expected in out["error"]


def test_unresolvable_chat_is_an_error(allowlist):
    out = run(core.send(FakeClient(), resolve, Elicit(), allowlist, "@nobody", "x"))
    assert out["sent"] is False and "@nobody" in out["error"]


# --- allowlist file ---------------------------------------------------------

def test_missing_file_is_an_empty_list(allowlist):
    assert allowlist.chats() == []
    assert run(core.listed(allowlist)) == {"chats": []}


def test_file_is_written_atomically_with_0600(allowlist):
    allowed(allowlist, PETYA)
    data = json.loads(allowlist.path.read_text())
    assert [c["id"] for c in data["chats"]] == [42]
    assert stat.S_IMODE(allowlist.path.stat().st_mode) == 0o600
    assert not list(allowlist.path.parent.glob("*.tmp"))


@pytest.mark.parametrize("content", ["{not json", '{"chats": 5}', '[1, 2]', '{"chats": [{"title": "x"}]}'])
def test_corrupt_file_makes_every_tool_refuse(allowlist, content):
    allowlist.path.parent.mkdir(parents=True)
    allowlist.path.write_text(content)
    client, elicit = FakeClient(), Elicit()
    for out in (
        run(core.send(client, resolve, elicit, allowlist, "@petya", "x")),
        run(core.allow(client, resolve, elicit, allowlist, "@petya")),
        run(core.disallow(client, resolve, allowlist, "@petya")),
        run(core.listed(allowlist)),
    ):
        assert "send-allowlist.json" in out["error"]
    assert elicit.questions == [] and client.sent == []
    assert allowlist.path.read_text() == content


# --- allow / disallow -------------------------------------------------------

@pytest.mark.parametrize("chat_id, word", [(777000, "Telegram"), ("me", "Saved"), (50, "бот"),
                                           ("@news", "канал")])
def test_never_allowed_kinds_are_refused_without_asking(allowlist, chat_id, word):
    elicit = Elicit()
    out = run(core.allow(FakeClient(), resolve, elicit, allowlist, chat_id))
    assert elicit.questions == [] and allowlist.chats() == []
    assert out["allowed"] is False and word in out["error"]


def test_allow_accept_stores_the_chat(allowlist):
    elicit = Elicit()
    out = run(core.allow(FakeClient(), resolve, elicit, allowlist, "@club"))
    assert "«Клуб»" in elicit.questions[0] and "группа" in elicit.questions[0]
    assert "-1000000000008" in elicit.questions[0]
    assert out["allowed"] is True
    [entry] = allowlist.chats()
    assert entry["id"] == -1000000000008 and entry["type"] == "supergroup"
    assert entry["username"] == "club" and entry["title"] == "Клуб" and entry["added_at"]


def test_allow_decline_stores_nothing(allowlist):
    out = run(core.allow(FakeClient(), resolve, Elicit(action="decline"), allowlist, "@petya"))
    assert out["allowed"] is False and allowlist.chats() == []


def test_allow_twice_does_not_ask_again(allowlist):
    allowed(allowlist, PETYA)
    elicit = Elicit()
    out = run(core.allow(FakeClient(), resolve, elicit, allowlist, "Петя"))
    assert elicit.questions == [] and out["allowed"] is True and "уже" in out["note"]
    assert len(allowlist.chats()) == 1


def test_disallow_needs_no_confirmation(allowlist):
    allowed(allowlist, PETYA, FAMILY)
    out = run(core.disallow(FakeClient(), resolve, allowlist, "@petya"))
    assert out == {"removed": True, "chat_id": 42}
    assert [c["id"] for c in allowlist.chats()] == [-7]


def test_disallow_by_bare_id_when_the_chat_no_longer_resolves(allowlist):
    allowlist.add({"id": 999, "title": "Ушедший", "type": "user", "username": None,
                   "added_at": "2026-10-13T00:00:00Z"})
    out = run(core.disallow(FakeClient(), resolve, allowlist, "999"))
    assert out == {"removed": True, "chat_id": 999} and allowlist.chats() == []


def test_disallow_of_an_unlisted_chat_says_so(allowlist):
    out = run(core.disallow(FakeClient(), resolve, allowlist, "@petya"))
    assert out == {"removed": False, "chat_id": 42}


# --- the confirmation must show the whole text ---------------------------------

@pytest.mark.parametrize("text", ["я" * 3200, "&" * 700], ids=["long", "escapes"])
def test_text_that_would_be_cut_on_the_button_is_refused_before_asking(allowlist, text):
    # hermes' Telegram card fits ~3400 escaped UTF-16 units and cuts the rest
    # with "...": the user would accept a message he did not fully see.
    allowed(allowlist, PETYA)
    client, elicit = FakeClient(), Elicit()
    out = run(core.send(client, resolve, elicit, allowlist, "@petya", text))
    assert elicit.questions == [] and client.sent == []
    assert out["sent"] is False and "раздели" in out["error"]


def test_a_long_text_that_fits_the_button_is_asked(allowlist):
    allowed(allowlist, PETYA)
    elicit = Elicit()
    out = run(core.send(FakeClient(), resolve, elicit, allowlist, "@petya", "я" * 2800))
    assert out["sent"] is True and elicit.questions[0].endswith("я" * 2800)


# --- forum topics -------------------------------------------------------------

class MessageActionTopicCreate(SimpleNamespace):
    pass


TRIPS = SimpleNamespace(id=34, message="", action=MessageActionTopicCreate(title="Поездки"))


def test_text_goes_into_a_topic(allowlist):
    allowed(allowlist, CLUB)
    client, elicit = FakeClient(messages={(-1000000000008, 34): TRIPS}), Elicit()
    out = run(core.send(client, resolve, elicit, allowlist, "@club", "Едем", topic_id=34))
    assert elicit.questions[0].startswith("Отправить в «Клуб» (группа, @club), тема «Поездки»:")
    assert client.sent[0]["reply_to"] == 34 and out["sent"] is True


def test_a_message_that_is_not_a_topic_is_refused(allowlist):
    allowed(allowlist, CLUB)
    plain = SimpleNamespace(id=35, message="привет", action=None)
    client, elicit = FakeClient(messages={(-1000000000008, 35): plain}), Elicit()
    out = run(core.send(client, resolve, elicit, allowlist, "@club", "x", topic_id=35))
    assert elicit.questions == [] and client.sent == []
    assert out["sent"] is False and "не тема" in out["error"]


def test_a_missing_topic_is_refused(allowlist):
    allowed(allowlist, CLUB)
    client, elicit = FakeClient(), Elicit()
    out = run(core.send(client, resolve, elicit, allowlist, "@club", "x", topic_id=99))
    assert elicit.questions == [] and client.sent == [] and "не тема" in out["error"]


def test_topic_and_reply_together_are_refused(allowlist):
    allowed(allowlist, CLUB)
    client, elicit = FakeClient(messages={(-1000000000008, 34): TRIPS}), Elicit()
    out = run(core.send(client, resolve, elicit, allowlist, "@club", "x", topic_id=34,
                        reply_to_message_id=36))
    assert elicit.questions == [] and client.sent == [] and "topic_id" in out["error"]


# --- files ----------------------------------------------------------------------

@pytest.fixture
def shared(tmp_path, monkeypatch):
    root = tmp_path / "shared"
    (root / "outbox").mkdir(parents=True)
    monkeypatch.setattr(core, "SHARED_DIR", root)
    return root


def put(shared, name, size=1000):
    path = shared / "outbox" / name
    path.write_bytes(b"x" * size)
    return str(path)


def test_one_file_without_caption_goes_as_a_document(allowlist, shared):
    allowed(allowlist, PETYA)
    client, elicit = FakeClient(), Elicit()
    path = put(shared, "the_user_tickets.pdf", 37 * 1024)
    out = run(core.send(client, resolve, elicit, allowlist, "@petya", files=[path]))
    assert elicit.questions[0] == "Отправить в «Петя Иванов» (личка, @petya):\n📎 the_user_tickets.pdf (37 KB)"
    assert client.sent == [{"to": 42, "file": path, "caption": "", "parse_mode": None,
                            "reply_to": None, "force_document": True}]
    assert out == {"sent": True, "chat": "Петя Иванов", "chat_id": 42, "message_ids": [2000]}


def test_several_files_with_a_caption_go_into_a_topic_as_one_group(allowlist, shared):
    allowed(allowlist, CLUB)
    client = FakeClient(messages={(-1000000000008, 34): TRIPS})
    elicit = Elicit()
    a, b = put(shared, "the user.pdf"), put(shared, "Maria.pdf", 3 * 1024 * 1024)
    out = run(core.send(client, resolve, elicit, allowlist, "@club", "Посадочные", parse_mode="md",
                        topic_id=34, files=[a, "outbox/Maria.pdf"]))
    q = elicit.questions[0]
    assert "тема «Поездки»" in q and "📎 the user.pdf (1000 B)" in q and "📎 Maria.pdf (3.0 MB)" in q
    assert q.endswith("\n\nПосадочные")
    [call] = client.sent
    assert call["file"] == [a, b] and call["caption"] == "Посадочные" and call["parse_mode"] == "md"
    assert call["reply_to"] == 34 and call["force_document"] is True
    assert out["message_ids"] == [2000, 2001]


def test_files_declined_send_nothing(allowlist, shared):
    allowed(allowlist, PETYA)
    client = FakeClient()
    out = run(core.send(client, resolve, Elicit(action="decline"), allowlist, "@petya",
                        files=[put(shared, "a.pdf")]))
    assert client.sent == [] and out["sent"] is False and out["reason"]


def _escape_link(shared, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("x")
    (shared / "outbox" / "link.pdf").symlink_to(secret)
    return [str(shared / "outbox" / "link.pdf")]


@pytest.mark.parametrize("make, expected", [
    (lambda shared, tmp: [str(tmp / "elsewhere.pdf")], "/outbox/"),
    (lambda shared, tmp: ["outbox/../../elsewhere.pdf"], "/outbox/"),
    (lambda shared, tmp: ["/etc/passwd"], "/outbox/"),
    (_escape_link, "/outbox/"),
    (lambda shared, tmp: [str(shared / "outbox" / "nope.pdf")], "файла нет"),
    (lambda shared, tmp: [str(shared / "outbox")], "файла нет"),
    (lambda shared, tmp: [], "хотя бы один"),
    (lambda shared, tmp: [""], "пустой путь"),
    (lambda shared, tmp: [put(shared, f"f{i}.pdf") for i in range(11)], "больше 10"),
])
def test_bad_files_are_refused_before_asking(allowlist, shared, tmp_path, make, expected):
    (tmp_path / "elsewhere.pdf").write_text("x")
    allowed(allowlist, PETYA)
    client, elicit = FakeClient(), Elicit()
    out = run(core.send(client, resolve, elicit, allowlist, "@petya", files=make(shared, tmp_path)))
    assert elicit.questions == [] and client.sent == []
    assert out["sent"] is False and expected in out["error"]


def test_files_over_the_size_limit_are_refused(allowlist, shared, monkeypatch):
    monkeypatch.setattr(core, "MAX_FILES_BYTES", 1500)
    allowed(allowlist, PETYA)
    client, elicit = FakeClient(), Elicit()
    out = run(core.send(client, resolve, elicit, allowlist, "@petya",
                        files=[put(shared, "a.pdf"), put(shared, "b.pdf")]))
    assert elicit.questions == [] and client.sent == [] and "Drive" in out["error"]


def test_a_caption_over_1024_is_refused(allowlist, shared):
    allowed(allowlist, PETYA)
    client, elicit = FakeClient(), Elicit()
    out = run(core.send(client, resolve, elicit, allowlist, "@petya", "x" * 1025,
                        files=[put(shared, "a.pdf")]))
    assert elicit.questions == [] and client.sent == [] and "1024" in out["error"]


def test_file_lines_count_toward_the_button_budget(allowlist, shared):
    allowed(allowlist, PETYA)
    client, elicit = FakeClient(), Elicit()
    files = [put(shared, f"{i}_" + "&" * 40 + ".pdf") for i in range(10)]  # & escapes to 5 units
    out = run(core.send(client, resolve, elicit, allowlist, "@petya", "x" * 1000, files=files))
    assert elicit.questions == [] and client.sent == [] and "раздели" in out["error"]


def test_text_is_still_required_without_files(allowlist, shared):
    allowed(allowlist, PETYA)
    out = run(core.send(FakeClient(), resolve, Elicit(), allowlist, "@petya"))
    assert out["sent"] is False and "пуст" in out["error"]


def test_a_file_telegram_mcp_cannot_read_is_refused_before_asking(allowlist, shared, monkeypatch):
    # hermes runs under umask 077: a file it copies is 0600 root, and
    # telegram-mcp runs as uid 10001. Live 2026-10-14 this surfaced only after
    # the accept, so the user had to confirm twice.
    allowed(allowlist, PETYA)
    path = put(shared, "a.pdf")
    monkeypatch.setattr(core.os, "access", lambda p, mode: False)
    client, elicit = FakeClient(), Elicit()
    out = run(core.send(client, resolve, elicit, allowlist, "@petya", files=[path]))
    assert elicit.questions == [] and client.sent == []
    assert f"chmod 644 {path}" in out["error"]
