"""Make a Yandex Station say a phrase, through the Quasar cloud scenario.

There is no official API for arbitrary speech. This follows what
AlexxIT/YandexStation does (core/yandex_session.py, core/yandex_quasar.py,
read 2026-11-06): one service scenario per speaker, whose single step is a
`tts` action; to speak, rewrite the step's text and run the scenario.
Verified on the user's Station Mini on 2026-11-06 (spec
design notes not published).

The x_token is a long-lived credential for the user's whole Yandex account.
It, the session cookies and the CSRF token never reach a log line.
"""
import asyncio
import enum
import logging
import re

from aiohttp import ClientSession, ClientTimeout, CookieJar

log = logging.getLogger("alice_bridge.quasar")

SCENARIO_NAME = "Гермес озвучка"
# Yandex insists on letters in a voice trigger, so the device id is spelled
# with Cyrillic ones; nobody will ever say it.
MASK_EN = "0123456789abcdef-"
MASK_RU = "оеаинтсрвлкмдпуяы"
REQUEST_TIMEOUT = ClientTimeout(total=3)
# hermes waits 5 s for /speak; give up before it does, so a phrase is not
# spoken after Telegram already said it was not.
SAY_DEADLINE_S = 4.5
_CSRF = re.compile(r'"csrfToken2":"(.+?)"')


class Speech(enum.StrEnum):
    OK = "ok"
    AUTH_LOST = "auth_lost"        # the x_token is no longer accepted: the user must log in again
    OFFLINE = "offline"            # the speaker is not online (or not in the account)
    REJECTED = "rejected"          # Yandex answered status=error (e.g. text over 100 chars)
    ERROR = "error"                # network, unexpected status or page shape
    NOT_CONFIGURED = "not_configured"
    QUEUED = "queued"              # accepted; spoken after the Station's current phrase (pacing.py)


class _Failed(Exception):
    def __init__(self, speech: Speech, detail: str):
        super().__init__(detail)
        self.speech = speech
        self.detail = detail


def http_session(*, unsafe: bool = False) -> ClientSession:
    """The HTTP session alice-bridge uses, Yandex calls included.

    Yandex session cookies (L, Session_id) carry / + = and must go back
    unquoted, as in the spike and AlexxIT ("fix bug with wrong CSRF token
    response"); aiohttp's default jar quotes them.
    """
    return ClientSession(cookie_jar=CookieJar(quote_cookie=False, unsafe=unsafe))


def encode(uid: str) -> str:
    return "".join(MASK_RU[MASK_EN.index(ch)] for ch in uid)


def tts_scenario(device_id: str, text: str) -> dict:
    return {
        "name": SCENARIO_NAME,
        "icon": "home",
        "triggers": [{"trigger": {"type": "scenario.trigger.voice", "value": encode(device_id)}}],
        "steps": [{
            "type": "scenarios.steps.actions.v2",
            "parameters": {"items": [{
                "id": device_id,
                "type": "step.action.item.device",
                "value": {"id": device_id, "item_type": "device", "capabilities": [{
                    "type": "devices.capabilities.quasar",
                    "state": {"instance": "tts", "value": {"text": text}},
                }]},
            }]},
        }],
    }


class QuasarSpeaker:
    def __init__(self, http: ClientSession, x_token: str, *,
                 mobileproxy: str = "https://mobileproxy.passport.yandex.net",
                 yandex: str = "https://yandex.ru",
                 iot: str = "https://iot.quasar.yandex.ru"):
        self._http = http
        self._x_token = x_token
        self._mobileproxy = mobileproxy
        self._yandex = yandex
        self._iot = iot
        self._csrf: str | None = None
        self._logged_in = False
        self._scenario_ids: dict[str, str] = {}
        # One scenario per speaker: a second phrase must not rewrite the text
        # between the first one's update and run.
        self._locks: dict[str, asyncio.Lock] = {}

    async def start(self) -> Speech:
        """Log in ahead of the first phrase, so it does not pay for it."""
        try:
            result = await self._login()
        except _Failed as failed:
            result = failed.speech
            log.warning("yandex login: %s (%s)", result, failed.detail)
        except Exception as exc:  # a Yandex change must stop speech, never the bridge
            result = Speech.ERROR
            log.warning("yandex login: error (%s)", type(exc).__name__)
        else:
            log.info("yandex login: %s", result)
        return result

    async def say(self, device_id: str, text: str) -> Speech:
        try:
            async with asyncio.timeout(SAY_DEADLINE_S):
                return await self._say(device_id, text)
        except TimeoutError:
            log.warning("speech error: deadline of %.1fs passed", SAY_DEADLINE_S)
            return Speech.ERROR

    async def _say(self, device_id: str, text: str) -> Speech:
        lock = self._locks.setdefault(device_id, asyncio.Lock())
        async with lock:
            try:
                if not self._logged_in:
                    result = await self._login()
                    if result is not Speech.OK:
                        return result
                if not await self._online(device_id):
                    return Speech.OFFLINE
                cached = device_id in self._scenario_ids
                try:
                    await self._speak(await self._scenario_id(device_id), device_id, text)
                except _Failed as failed:
                    if not cached or failed.speech is Speech.AUTH_LOST:
                        raise
                    # The scenario may have been deleted or recreated in the app.
                    log.info("speech scenario lookup again after %s", failed.speech)
                    self._scenario_ids.pop(device_id, None)
                    await self._speak(await self._scenario_id(device_id), device_id, text)
                return Speech.OK
            except _Failed as failed:
                log.warning("speech %s: %s", failed.speech, failed.detail)
                return failed.speech
            except Exception as exc:  # unexpected page or JSON shape included
                log.warning("speech error: %s", type(exc).__name__)
                return Speech.ERROR

    async def _speak(self, sid: str, device_id: str, text: str) -> None:
        await self._call("put", f"{self._iot}/m/v4/user/scenarios/{sid}", tts_scenario(device_id, text))
        await self._call("post", f"{self._iot}/m/user/scenarios/{sid}/actions")

    async def _login(self) -> Speech:
        """x_token -> session cookies on .yandex.ru -> CSRF token."""
        self._logged_in = False
        async with self._http.post(
                f"{self._mobileproxy}/1/bundle/auth/x_token/",
                data={"type": "x-token", "retpath": "https://www.yandex.ru"},
                headers={"Ya-Consumer-Authorization": f"OAuth {self._x_token}"},
                timeout=REQUEST_TIMEOUT) as r:
            resp = await r.json(content_type=None)
        if resp.get("status") != "ok":
            log.error("yandex refused the x_token: %s", resp.get("errors"))
            return Speech.AUTH_LOST
        async with self._http.get(f"{resp['passport_host']}/auth/session/",
                                  params={"track_id": resp["track_id"]},
                                  allow_redirects=False, timeout=REQUEST_TIMEOUT) as r:
            location = r.headers.get("Location", "")
            status = r.status
        if "/auth/finish" not in location:
            raise _Failed(Speech.ERROR, f"no session from passport (HTTP {status})")
        await self._refresh_csrf()
        self._logged_in = True
        return Speech.OK

    async def _refresh_csrf(self) -> None:
        async with self._http.get(f"{self._yandex}/iot", timeout=REQUEST_TIMEOUT) as r:
            page = await r.text()
            status = r.status
        found = _CSRF.search(page)
        if not found:
            raise _Failed(Speech.ERROR, f"no CSRF token on yandex.ru/iot (HTTP {status})")
        self._csrf = found[1]

    async def _call(self, method: str, url: str, payload: dict | None = None) -> dict:
        """One Quasar request: re-login once on 401, re-read CSRF once on 403."""
        path = url.removeprefix(self._iot)
        for attempt in (1, 2):
            headers = {"x-csrf-token": self._csrf or ""} if method != "get" else {}
            async with self._http.request(method, url, json=payload, headers=headers,
                                          timeout=REQUEST_TIMEOUT) as r:
                status = r.status
                body = await r.json(content_type=None) if status == 200 else None
            if status == 200:
                if isinstance(body, dict) and body.get("status") == "ok":
                    return body
                code = body.get("code") if isinstance(body, dict) else None
                raise _Failed(Speech.REJECTED, f"{method.upper()} {path}: code={code}")
            if attempt == 1 and status == 401:
                result = await self._login()
                if result is not Speech.OK:
                    raise _Failed(result, f"{method.upper()} {path}: HTTP 401, login again failed")
                continue
            if attempt == 1 and status == 403:
                await self._refresh_csrf()
                continue
            raise _Failed(Speech.ERROR, f"{method.upper()} {path}: HTTP {status}")
        raise AssertionError("unreachable")

    async def _online(self, device_id: str) -> bool:
        body = await self._call("get", f"{self._iot}/m/v3/user/devices")
        for house in body.get("households") or []:
            for device in house.get("all") or []:
                if device.get("id") == device_id:
                    return device.get("state") == "online"
        return False

    async def _scenario_id(self, device_id: str) -> str:
        if sid := self._scenario_ids.get(device_id):
            return sid
        trigger = encode(device_id)
        body = await self._call("get", f"{self._iot}/m/user/scenarios")
        for scenario in body.get("scenarios") or []:
            triggers = scenario.get("triggers") or [{}]
            if triggers[0].get("value") == trigger:
                sid = scenario["id"]
                break
        else:
            created = await self._call("post", f"{self._iot}/m/v4/user/scenarios",
                                       tts_scenario(device_id, "пустышка"))
            sid = created["scenario_id"]
            log.info("created the speech scenario for %s", device_id)
        self._scenario_ids[device_id] = sid
        return sid
