"""Webhook for the user's private Alice skill «Агент Гермес».

Alice gives a skill about 3 s to answer; a hermes turn takes far longer. So
this service only authenticates the request, hands the phrase to hermes'
webhook platform (which answers 202 at once and runs the agent in the
background) and tells Alice it was passed on. The agent's reply goes to
Telegram. Nothing the assistant says is sent back to Yandex.

Alice cannot sign its requests, so they are checked here: a secret URL path,
the skill id and, optionally, the Yandex user id. hermes' side is signed with
HMAC and stays on the internal compose network.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
from dataclasses import dataclass

from aiohttp import ClientSession, ClientTimeout, web

log = logging.getLogger("alice_bridge")

# Alice's own limit is ~3 s for the whole round trip through Yandex.
FORWARD_TIMEOUT_S = 1.5
# Alice speaks at most 1024 characters.
MAX_TEXT = 1024

ASK = "Что передать ассистенту?"
HELP = ("Я передаю голосовые команды личному ассистенту пользователя, "
        "а он отвечает в Telegram. Например: попроси агента Гермеса "
        "добавить задачу купить молоко.")
PASSED = "Передал: "
FAILED = "Не получилось передать, ассистент недоступен."
HELP_COMMANDS = {"помощь", "что ты умеешь", "помоги"}


@dataclass(frozen=True)
class Config:
    path_secret: str
    webhook_secret: str
    skill_id: str
    allowed_user_ids: frozenset[str]
    hermes_url: str

    @classmethod
    def from_env(cls) -> "Config":
        users = os.environ.get("ALICE_ALLOWED_USER_IDS", "")
        return cls(
            path_secret=os.environ.get("ALICE_PATH_SECRET", ""),
            webhook_secret=os.environ.get("ALICE_WEBHOOK_SECRET", ""),
            skill_id=os.environ.get("ALICE_SKILL_ID", ""),
            allowed_user_ids=frozenset(u.strip() for u in users.split(",") if u.strip()),
            hermes_url=os.environ.get("HERMES_WEBHOOK_URL", "http://hermes:8644/webhooks/alice"),
        )


def reply(text: str, end_session: bool = True) -> web.Response:
    return web.json_response({
        "version": "1.0",
        "response": {"text": text[:MAX_TEXT], "end_session": end_session},
    })


def sign(body: bytes, secret: str) -> str:
    """hermes' generic X-Webhook-Signature: hex HMAC-SHA256 of the raw body."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def phrase(request: dict) -> str:
    """What the user asked for, without the activation phrase.

    `command` is Alice's cleaned-up form of the request (no punctuation,
    numbers as digits) and, unlike `original_utterance`, never carries
    "попроси агента Гермеса". The agent copes with the missing punctuation.
    """
    return (request.get("command") or request.get("original_utterance") or "").strip()


class Bridge:
    def __init__(self, config: Config):
        self.config = config
        self.http: ClientSession | None = None

    def _authorized(self, session: dict) -> bool:
        cfg = self.config
        if not (cfg.skill_id and cfg.webhook_secret):
            log.error("rejected: ALICE_SKILL_ID or ALICE_WEBHOOK_SECRET is not set")
            return False
        skill_id = session.get("skill_id", "")
        user_id = (session.get("user") or {}).get("user_id", "")
        if not hmac.compare_digest(skill_id, cfg.skill_id):
            log.warning("rejected: skill_id=%s", skill_id)
            return False
        if cfg.allowed_user_ids and user_id not in cfg.allowed_user_ids:
            log.warning("rejected: user_id=%s", user_id)
            return False
        return True

    async def forward(self, text: str, delivery_id: str) -> bool:
        body = json.dumps({"text": text, "source": "alice"}, ensure_ascii=False).encode()
        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Signature": sign(body, self.config.webhook_secret),
            # hermes drops a repeated X-Request-ID, so an Alice retry of the
            # same message does not run the agent twice.
            "X-Request-ID": delivery_id,
        }
        try:
            async with self.http.post(self.config.hermes_url, data=body, headers=headers,
                                      timeout=ClientTimeout(total=FORWARD_TIMEOUT_S)) as resp:
                ok = resp.status in (200, 202)
                log.info("forwarded delivery=%s status=%s", delivery_id, resp.status)
                return ok
        except (asyncio.TimeoutError, OSError) as exc:
            log.warning("forward failed delivery=%s (%s)", delivery_id, type(exc).__name__)
            return False

    async def handle(self, request: web.Request) -> web.Response:
        secret = self.config.path_secret
        if not secret or not hmac.compare_digest(request.match_info["secret"], secret):
            raise web.HTTPNotFound()
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise web.HTTPBadRequest()
        if not isinstance(body, dict):
            raise web.HTTPBadRequest()
        session = body.get("session") or {}
        if not self._authorized(session):
            raise web.HTTPForbidden()

        req = body.get("request") or {}
        text = phrase(req)
        log.debug("utterance: %s", text)
        if req.get("original_utterance", "").strip().lower() == "ping":
            return reply("pong")
        if text == "on_interrupt":
            return reply("Пока.")
        if text.lower() in HELP_COMMANDS:
            return reply(HELP, end_session=False)
        if not text:
            return reply(ASK, end_session=False)

        delivery_id = f"alice:{session.get('session_id', '')}:{session.get('message_id', '')}"
        if await self.forward(text, delivery_id):
            return reply(PASSED + text)
        return reply(FAILED)


async def health(_: web.Request) -> web.Response:
    return web.Response(text="ok")


@web.middleware
async def log_rejections(request: web.Request, handler):
    """One line per refused request, so a skill pointed at the wrong URL shows
    up in the logs. Never the path itself: it carries the secret."""
    try:
        return await handler(request)
    except web.HTTPException as exc:
        if exc.status >= 400:
            log.warning("refused %s request: HTTP %s", request.method, exc.status)
        raise


def make_app(config: Config) -> web.Application:
    bridge = Bridge(config)

    async def client(app: web.Application):
        bridge.http = ClientSession()
        yield
        await bridge.http.close()

    app = web.Application(client_max_size=64 * 1024, middlewares=[log_rejections])
    app.cleanup_ctx.append(client)
    app.router.add_get("/health", health)
    app.router.add_post("/alice/{secret}", bridge.handle)
    return app


def configure_logging() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # aiohttp's access log prints the request path, which carries the secret.
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


def main() -> None:
    configure_logging()
    web.run_app(make_app(Config.from_env()), host="0.0.0.0", port=8000, access_log=None)
