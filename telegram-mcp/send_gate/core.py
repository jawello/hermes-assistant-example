"""Sending from the user's Telegram account: an allowlist and a confirmation.

The bot may write only to chats on an allowlist, and every message leaves only
after the user accepts it on a button that shows the recipient and the exact
source text. The allowlist grows only through `allow`, which asks its own
confirmation; `disallow` asks nothing, since it only narrows what the bot can
do. Anything that is not an explicit accept — a refusal, a timeout, an error,
a cancelled call — sends nothing.

The Telegram client, the resolver and the elicit function are parameters, so
this module needs neither Telethon nor upstream's server to be tested.
tools.py wires it to upstream's `mcp`.
Design: design notes not published
"""
import asyncio
import html
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

MAX_TEXT = 4096  # Telegram's limit for one message
MAX_CAPTION = 1024  # Telegram's limit for a file's caption
MAX_FILES = 10  # one document group
# Uploaded only after the accept, inside upstream's 240 s per call, of which
# the button may already have taken 180 s.
MAX_FILES_BYTES = 50 * 1024 * 1024
# The shared_files volume, mounted read-only: hermes copies a file into
# /shared/outbox/, gmail-mcp saves mail attachments under /shared/mail/.
SHARED_DIR = Path(os.environ.get("SHARED_DIR", "/shared"))
PARSE_MODES = {None: None, "md": "markdown", "html": "html"}
SERVICE_ID = 777000  # «Telegram»: login codes arrive here
# Upstream cuts every tool call at 240 s and hermes waits 300 s; answering
# first gives the bot a clear reason instead of a generic timeout.
CONFIRM_TIMEOUT = 180
# hermes' Telegram approval card holds 4096 UTF-16 units of HTML, header,
# reason and deadline included (~3400 left for the text), and cuts a longer
# preview with "...". the user must see the whole message he accepts, so a
# confirmation over this budget is refused instead of shown cut.
CONFIRM_BUDGET = 3000
NEVER = {
    "service": "это служебный чат «Telegram» (туда приходят коды входа)",
    "self": "это Saved Messages — пользователь пишет туда сам",
    "bot": "это бот",
    "channel": "это канал: писать в каналы нельзя",
}
LABELS = {"user": "личка", "group": "группа", "supergroup": "группа"}


class AllowlistError(Exception):
    """The allowlist file exists but cannot be trusted."""


class Approval(BaseModel):
    # Defaulted, not required: hermes answers an accepted elicitation with
    # `content={}`, and a required field would turn that approval into a
    # validation error. Consent is `action == "accept"`.
    confirm: bool = Field(default=True, description="Подтвердить?")


# --- entities -------------------------------------------------------------

def _cls(entity) -> str:
    return type(entity).__name__


def peer_id(entity) -> int:
    """Telethon's get_peer_id: user id, -id for a group, -(10**12 + id) for a channel."""
    name = _cls(entity)
    if name.startswith("Channel"):
        return -(10**12 + entity.id)
    if name.startswith("Chat"):
        return -entity.id
    return entity.id


def kind(entity, me_id: int) -> str:
    name = _cls(entity)
    if name.startswith("Channel"):
        return "channel" if getattr(entity, "broadcast", False) else "supergroup"
    if name.startswith("Chat"):
        return "group"
    if entity.id == SERVICE_ID:
        return "service"
    if getattr(entity, "is_self", False) or entity.id == me_id:
        return "self"
    if getattr(entity, "bot", False):
        return "bot"
    return "user"


def title(entity) -> str:
    if getattr(entity, "title", None):
        return entity.title
    parts = [getattr(entity, "first_name", None), getattr(entity, "last_name", None)]
    return " ".join(p for p in parts if p) or str(entity.id)


def _who(entity, entity_kind: str) -> str:
    label = LABELS.get(entity_kind, entity_kind)
    username = getattr(entity, "username", None)
    return f"«{title(entity)}» ({label}" + (f", @{username}" if username else "") + ")"


# --- allowlist --------------------------------------------------------------

class Allowlist:
    def __init__(self, path):
        self.path = Path(path)

    def chats(self) -> list[dict]:
        try:
            raw = self.path.read_text()
        except FileNotFoundError:
            return []
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise AllowlistError(f"{self.path} не читается как JSON ({exc})")
        chats = data.get("chats") if isinstance(data, dict) else None
        if not isinstance(chats, list) or not all(
                isinstance(c, dict) and isinstance(c.get("id"), int) for c in chats):
            raise AllowlistError(f"{self.path}: ожидался объект {{\"chats\": [{{\"id\": …}}]}}")
        return chats

    def has(self, chat_id: int) -> bool:
        return any(c["id"] == chat_id for c in self.chats())

    def _write(self, chats: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps({"chats": chats}, ensure_ascii=False, indent=1))
        tmp.chmod(0o600)
        os.replace(tmp, self.path)

    def add(self, entry: dict) -> None:
        chats = self.chats()
        if not any(c["id"] == entry["id"] for c in chats):
            self._write(chats + [entry])

    def remove(self, chat_id: int) -> bool:
        chats = self.chats()
        kept = [c for c in chats if c["id"] != chat_id]
        if len(kept) == len(chats):
            return False
        self._write(kept)
        return True


def _broken(exc: AllowlistError) -> str:
    return f"список разрешённых чатов повреждён, ничего не делаю: {exc}"


# --- confirmation -----------------------------------------------------------

async def ask(elicit, question: str) -> str | None:
    """None on an explicit yes; otherwise why not. Every other path refuses."""
    try:
        result = await asyncio.wait_for(elicit(message=question, schema=Approval), CONFIRM_TIMEOUT)
    except TimeoutError:
        return "пользователь не подтвердил вовремя"
    except Exception as exc:  # noqa: BLE001 — no way to ask is not permission
        return f"подтвердить не удалось ({type(exc).__name__})"
    action = getattr(result, "action", None)
    if action != "accept":
        return f"пользователь не подтвердил ({action})"
    data = getattr(result, "data", None)
    if not isinstance(data, Approval) or not data.confirm:
        return "пользователь не подтвердил"
    return None


async def _resolve(resolve, client, chat_id):
    try:
        return await resolve(chat_id, client), None
    except Exception as exc:  # noqa: BLE001
        return None, f"чат {chat_id!r} не найден ({exc})"


def _utf16(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _excerpt(text: str, limit: int = 100) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{round(n / 1024)} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def check_files(files) -> tuple[list[Path] | None, str | None]:
    """The files to send, resolved inside SHARED_DIR, or why not."""
    root = SHARED_DIR.resolve()
    if not isinstance(files, list) or not files:
        return None, "files: нужен хотя бы один путь"
    if len(files) > MAX_FILES:
        return None, f"файлов {len(files)}, больше {MAX_FILES} — отправь несколькими сообщениями"
    paths = []
    for raw in files:
        if not isinstance(raw, str) or not raw.strip():
            return None, "files: пустой путь"
        path = Path(raw)
        # Symlinks resolved: a link inside the folder must not point out of it.
        path = (path if path.is_absolute() else root / path).resolve()
        if not path.is_relative_to(root):
            return None, f"{raw}: файл должен лежать в {root} — скопируй его в {root}/outbox/"
        if not path.is_file():
            return None, f"{raw}: файла нет (или это не файл)"
        # hermes writes under umask 077 and this server is not root: found
        # here, not after the user's accept.
        if not os.access(path, os.R_OK):
            return None, f"{raw}: telegram-mcp не может прочитать файл — выполни chmod 644 {path}"
        paths.append(path)
    total = sum(p.stat().st_size for p in paths)
    if total > MAX_FILES_BYTES:
        return None, (f"файлы весят {_size(total)}, больше {_size(MAX_FILES_BYTES)} — "
                      "положи их на Drive и отправь ссылку")
    return paths, None


async def _topic_title(client, entity, topic_id) -> tuple[str | None, str | None]:
    """A forum topic's id is the id of the service message that created it.

    The title is the one the topic was created with: a later rename is not
    looked up.
    """
    created = await client.get_messages(entity, ids=topic_id)
    action = getattr(created, "action", None)
    if created is None or _cls(action) != "MessageActionTopicCreate":
        return None, f"{topic_id} — это не тема этого чата (id темы бери из list_topics)"
    return action.title, None


# --- tools ------------------------------------------------------------------

async def send(client, resolve, elicit, allowlist: Allowlist, chat_id, text: str = "",
               parse_mode: str | None = None, reply_to_message_id: int | None = None,
               topic_id: int | None = None, files: list[str] | None = None) -> dict:
    def refuse(error):
        return {"sent": False, "error": error}

    text = text or ""
    if files is None:
        if not text.strip():
            return refuse("текст пустой")
        if len(text) > MAX_TEXT:
            return refuse(f"текст длиннее {MAX_TEXT} символов ({len(text)}) — разбей на несколько сообщений")
    elif len(text) > MAX_CAPTION:
        return refuse(f"подпись к файлам длиннее {MAX_CAPTION} символов ({len(text)}) — "
                      "отправь текст отдельным сообщением")
    if parse_mode not in PARSE_MODES:
        return refuse(f"parse_mode {parse_mode!r} не поддерживается: только md, html или без него")
    if topic_id is not None and reply_to_message_id is not None:
        return refuse("topic_id и reply_to_message_id вместе нельзя: "
                      "ответ на сообщение сам попадает в его тему")
    paths = None
    if files is not None:
        paths, error = check_files(files)
        if error:
            return refuse(error)
    try:
        allowlist.chats()
    except AllowlistError as exc:
        return refuse(_broken(exc))

    entity, error = await _resolve(resolve, client, chat_id)
    if error:
        return refuse(error)
    me = await client.get_me()
    entity_kind = kind(entity, me.id)
    target = peer_id(entity)
    if not allowlist.has(target):
        return refuse(f"Чат «{title(entity)}» не в списке разрешённых — пользователь может "
                      "разрешить его через allow_chat")

    header = f"Отправить в {_who(entity, entity_kind)}"
    if topic_id is not None:
        topic, error = await _topic_title(client, entity, topic_id)
        if error:
            return refuse(error)
        header += f", тема «{topic}»"
    lines = [header + (f", {PARSE_MODES[parse_mode]}" if parse_mode else "") + ":"]
    if reply_to_message_id is not None:
        original = await client.get_messages(entity, ids=reply_to_message_id)
        if original is None:
            return refuse(f"сообщения {reply_to_message_id} в этом чате нет")
        lines.append(f"↩ в ответ на: «{_excerpt(getattr(original, 'message', ''))}»")
    lines += [f"📎 {p.name} ({_size(p.stat().st_size)})" for p in paths or []]
    question = "\n".join(lines) + (f"\n\n{text}" if text else "")
    if _utf16(html.escape(question)) > CONFIRM_BUDGET:
        return refuse("текст не поместится на кнопке подтверждения целиком — "
                      "раздели его на несколько сообщений")

    refusal = await ask(elicit, question)
    if refusal:
        return {"sent": False, "reason": refusal}
    try:
        # parse_mode=None explicitly: Telethon's client default is markdown.
        # A topic is addressed as a reply to the message that created it.
        reply_to = topic_id if topic_id is not None else reply_to_message_id
        if paths is None:
            message = await client.send_message(entity, text, parse_mode=parse_mode, reply_to=reply_to)
        else:
            # One file as a string: Telethon sends a list as a group. Always
            # documents: a group cannot mix photos and PDFs.
            file = str(paths[0]) if len(paths) == 1 else [str(p) for p in paths]
            sent = await client.send_file(entity, file, caption=text, parse_mode=parse_mode,
                                          reply_to=reply_to, force_document=True)
    except Exception as exc:  # noqa: BLE001
        return refuse(f"Telegram не принял сообщение: {type(exc).__name__}: {exc}")
    done = {"sent": True, "chat": title(entity), "chat_id": target}
    if paths is None:
        return done | {"message_id": message.id}
    return done | {"message_ids": [m.id for m in (sent if isinstance(sent, list) else [sent])]}


async def allow(client, resolve, elicit, allowlist: Allowlist, chat_id) -> dict:
    try:
        allowlist.chats()
    except AllowlistError as exc:
        return {"allowed": False, "error": _broken(exc)}
    entity, error = await _resolve(resolve, client, chat_id)
    if error:
        return {"allowed": False, "error": error}
    me = await client.get_me()
    entity_kind = kind(entity, me.id)
    if entity_kind in NEVER:
        return {"allowed": False, "error": f"«{title(entity)}» разрешить нельзя: {NEVER[entity_kind]}"}
    target = peer_id(entity)
    if allowlist.has(target):
        return {"allowed": True, "chat": title(entity), "chat_id": target, "note": "уже в списке"}

    refusal = await ask(elicit, f"Разрешить боту писать в {_who(entity, entity_kind)}, id {target}?")
    if refusal:
        return {"allowed": False, "reason": refusal}
    allowlist.add({"id": target, "title": title(entity), "type": entity_kind,
                   "username": getattr(entity, "username", None),
                   "added_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    return {"allowed": True, "chat": title(entity), "chat_id": target}


async def disallow(client, resolve, allowlist: Allowlist, chat_id) -> dict:
    try:
        allowlist.chats()
    except AllowlistError as exc:
        return {"removed": False, "error": _broken(exc)}
    entity, error = await _resolve(resolve, client, chat_id)
    if entity is not None:
        target = peer_id(entity)
    else:
        # A chat that no longer resolves can still be removed by its bare id.
        try:
            target = int(chat_id)
        except (TypeError, ValueError):
            return {"removed": False, "error": error}
    return {"removed": allowlist.remove(target), "chat_id": target}


async def listed(allowlist: Allowlist) -> dict:
    try:
        return {"chats": allowlist.chats()}
    except AllowlistError as exc:
        return {"error": _broken(exc)}
