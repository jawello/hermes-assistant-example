"""Webhook for the user's private Alice skill «Агент Гермес», and its voice.

Alice gives a skill about 3 s to answer; a hermes turn takes far longer. So
this service only authenticates the request, hands the phrase to hermes'
webhook platform (which answers 202 at once and runs the agent in the
background) and tells Alice it was passed on. The full answer goes to
Telegram.

When the command was spoken to a known Station, hermes calls back on
`/speak` with a short phrase (at most 100 characters) when the turn ends,
and this service makes that Station say it through the Quasar cloud
scenario (quasar.py), never over a phrase it is still saying (pacing.py).
The Yandex x_token lives here only, never in hermes.

Alice cannot sign its requests, so they are checked here: a secret URL path,
the skill id and, optionally, the Yandex user id. hermes' side is signed with
HMAC both ways and stays on the internal compose network; the edge proxy
only forwards `/alice/*`.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass, field

from aiohttp import ClientSession, ClientTimeout, web

from alice_bridge.pacing import Pacer
from alice_bridge.quasar import QuasarSpeaker, Speech, http_session

log = logging.getLogger("alice_bridge")

# Alice's own limit is ~3 s for the whole round trip through Yandex.
FORWARD_TIMEOUT_S = 1.5
# Alice speaks at most 1024 characters.
MAX_TEXT = 1024
# Yandex refuses longer scenario text (QUASAR_SERVER_ACTION_LENGTH_ERROR).
MAX_SPOKEN = 100

ASK = "Что передать ассистенту?"
HELP = ("Я передаю голосовые команды личному ассистенту пользователя. "
        "Короткий ответ скажу здесь, полный придёт в Telegram. Например: "
        "попроси агента Гермеса добавить задачу купить молоко.")
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
    # Alice application_id of a Station -> its Quasar device id (ALICE_STATIONS).
    stations: dict[str, str] = field(default_factory=dict)
    # Yandex x_token for speech (YANDEX_X_TOKEN); empty means no speech.
    x_token: str = field(default="", repr=False)

    @classmethod
    def from_env(cls) -> "Config":
        users = os.environ.get("ALICE_ALLOWED_USER_IDS", "")
        return cls(
            path_secret=os.environ.get("ALICE_PATH_SECRET", ""),
            webhook_secret=os.environ.get("ALICE_WEBHOOK_SECRET", ""),
            skill_id=os.environ.get("ALICE_SKILL_ID", ""),
            allowed_user_ids=frozenset(u.strip() for u in users.split(",") if u.strip()),
            hermes_url=os.environ.get("HERMES_WEBHOOK_URL", "http://hermes:8644/webhooks/alice"),
            stations=parse_stations(os.environ.get("ALICE_STATIONS", "")),
            x_token=os.environ.get("YANDEX_X_TOKEN", ""),
        )


def parse_stations(raw: str) -> dict[str, str]:
    """`app_id=device_id,app_id2=device_id2` -> {app_id: device_id}."""
    pairs = (item.split("=", 1) for item in raw.split(",") if "=" in item)
    return {app.strip(): device.strip() for app, device in pairs if app.strip() and device.strip()}


def origin_speaker(body: dict, stations: dict[str, str]) -> str:
    """The Quasar device id of the Station the request came from, or "".

    Alice gives each device its own application_id; only a mapped one is a
    Station we can speak on. An unmapped origin is logged with its ids (not
    secrets), which is how a new Station gets mapped.
    """
    session = body.get("session") or {}
    app_id = (session.get("application") or {}).get("application_id", "")
    speaker = stations.get(app_id, "")
    if not speaker:
        client_id = (body.get("meta") or {}).get("client_id", "")
        log.info("origin not mapped: application_id=%s client_id=%s", app_id, client_id)
    return speaker


def reply(text: str, end_session: bool = True) -> web.Response:
    return web.json_response({
        "version": "1.0",
        "response": {"text": text[:MAX_TEXT], "end_session": end_session},
    })


# Signed requests older (or newer) than this are refused; hermes' own V2
# window is the same 300 s.
REPLAY_WINDOW_S = 300


def sign(message: bytes, secret: str) -> str:
    """Hex HMAC-SHA256."""
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def hermes_headers(body: bytes, secret: str, now: float | None = None) -> dict[str, str]:
    """hermes' generic webhook V2: HMAC of "<timestamp>.<body>", replay window 300 s."""
    ts = str(int(time.time() if now is None else now))
    return {"X-Webhook-Timestamp": ts, "X-Webhook-Signature-V2": sign(ts.encode() + b"." + body, secret)}


def speak_signature(body: bytes, secret: str, ts: str) -> str:
    """/speak signs "speak.<timestamp>.<body>": the "speak." prefix keeps a
    request to hermes from being valid here and the other way round."""
    return sign(b"speak." + ts.encode() + b"." + body, secret)


def _same(given: str, expected: str) -> bool:
    try:
        return hmac.compare_digest(given.encode("ascii"), expected.encode("ascii"))
    except UnicodeEncodeError:
        return False


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
        self.speaker = None
        self._seen: dict[str, float] = {}

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

    async def forward(self, text: str, delivery_id: str, speaker: str = "") -> bool:
        body = json.dumps({"text": text, "source": "alice", "speaker": speaker},
                          ensure_ascii=False).encode()
        headers = {
            "Content-Type": "application/json",
            **hermes_headers(body, self.config.webhook_secret),
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
        speaker = origin_speaker(body, self.config.stations)
        if await self.forward(text, delivery_id, speaker):
            # Open session: the Station listens again, so the next command
            # needs neither «Алиса» nor the skill's name (the user, 2026-11-09).
            return reply(PASSED + text, end_session=False)
        return reply(FAILED)


    def _fresh_speak_signature(self, request: web.Request, raw: bytes, secret: str) -> bool:
        ts = request.headers.get("X-Speak-Timestamp", "")
        given = request.headers.get("X-Speak-Signature", "")
        try:
            age = abs(time.time() - int(ts))
        except ValueError:
            return False
        if age > REPLAY_WINDOW_S or not given or not _same(given, speak_signature(raw, secret, ts)):
            return False
        # A signature is good once: a captured request cannot be spoken again.
        now = time.time()
        self._seen = {sig: at for sig, at in self._seen.items() if now - at <= REPLAY_WINDOW_S * 2}
        if given in self._seen:
            log.warning("refused a replayed /speak request")
            return False
        self._seen[given] = now
        return True

    async def speak(self, request: web.Request) -> web.Response:
        """hermes asks for a phrase on a Station: {"speaker": id, "text": phrase}."""
        raw = await request.read()
        secret = self.config.webhook_secret
        if not (secret and self._fresh_speak_signature(request, raw, secret)):
            raise web.HTTPUnauthorized()
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise web.HTTPBadRequest()
        if not isinstance(body, dict):
            raise web.HTTPBadRequest()
        device_id, text = body.get("speaker"), body.get("text")
        if not (isinstance(device_id, str) and device_id and isinstance(text, str) and text.strip()
                and len(text) <= MAX_SPOKEN):
            raise web.HTTPBadRequest()
        if self.speaker is None:
            result = Speech.NOT_CONFIGURED
        else:
            started = asyncio.get_running_loop().time()
            result = await self.speaker.say(device_id, text)
            log.info("spoke on %s: %s in %.2fs", device_id, result,
                     asyncio.get_running_loop().time() - started)
        log.debug("spoken text: %s", text)
        return web.json_response({"result": str(result)})


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


def make_app(config: Config, speaker_factory=None) -> web.Application:
    bridge = Bridge(config)
    factory = speaker_factory or QuasarSpeaker

    async def client(app: web.Application):
        bridge.http = http_session()
        if config.x_token:
            # Paced: a phrase never cuts the one the Station is saying.
            bridge.speaker = Pacer(factory(bridge.http, config.x_token))
            # Log in now, so the first phrase does not pay for it; a failure
            # is logged and retried on the first phrase.
            await bridge.speaker.start()
        yield
        if bridge.speaker is not None:
            await bridge.speaker.close()
        await bridge.http.close()

    app = web.Application(client_max_size=64 * 1024, middlewares=[log_rejections])
    app.cleanup_ctx.append(client)
    app.router.add_get("/health", health)
    app.router.add_post("/alice/{secret}", bridge.handle)
    app.router.add_post("/speak", bridge.speak)
    return app


def configure_logging() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # aiohttp's access log prints the request path, which carries the secret.
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


def main() -> None:
    configure_logging()
    web.run_app(make_app(Config.from_env()), host="0.0.0.0", port=8000, access_log=None)
