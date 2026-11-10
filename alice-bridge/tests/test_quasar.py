import asyncio
import logging

import pytest
from aiohttp import ClientSession, web

from alice_bridge.quasar import SCENARIO_NAME, QuasarSpeaker, Speech, encode, http_session

TOKEN = "x-token-secret-value"
DEVICE = "5a1e7c3d-0b9f-4e21-a6d4-7c2b9e0f1a35"


@pytest.fixture
async def yandex(aiohttp_server):
    """One fake server for passport, yandex.ru/iot and iot.quasar."""
    state = {
        "calls": [], "login_ok": True, "logins": 0, "csrf_n": 1, "device_state": "online",
        "scenarios": [{"id": "sc-1", "name": SCENARIO_NAME,
                       "triggers": [{"type": "scenario.trigger.voice", "value": encode(DEVICE)}]}],
        "unauth_once": False, "forbid_once": False, "put_error": None,
        "check_cookie": False, "bare_ok": False, "run_error": None, "put_delay": 0.05,
    }

    def csrf():
        return f"csrf-{state['csrf_n']}"

    async def x_token(request):
        state["logins"] += 1
        assert request.headers["Ya-Consumer-Authorization"] == f"OAuth {TOKEN}"
        if not state["login_ok"]:
            return web.json_response({"status": "error", "errors": ["oauth_token.invalid"]})
        if state["bare_ok"]:
            return web.json_response({"status": "ok"})
        return web.json_response({"status": "ok", "passport_host": state["base"], "track_id": "t1"})

    async def auth_session(request):
        resp = web.HTTPFound(f"{state['base']}/auth/finish?track_id=t1")
        # Yandex session cookies carry / + =; they must go back unquoted.
        resp.headers["Set-Cookie"] = "L=abc/def+gh==; Path=/"
        raise resp

    async def iot_page(request):
        return web.Response(text=f'<script>var x = {{"csrfToken2":"{csrf()}"}};</script>')

    async def devices(request):
        state["calls"].append(("devices",))
        if state["check_cookie"] and "L=abc/def+gh==" not in request.headers.get("Cookie", ""):
            return web.json_response({}, status=401)
        if state["unauth_once"]:
            state["unauth_once"] = False
            return web.json_response({}, status=401)
        return web.json_response({"status": "ok", "households": [{"name": "Кипр", "all": [
            {"id": DEVICE, "state": state["device_state"],
             "type": "devices.types.smart_speaker.yandex.station.mini_2"}]}]})

    async def scenarios(request):
        state["calls"].append(("scenarios",))
        return web.json_response({"status": "ok", "scenarios": state["scenarios"]})

    async def create(request):
        body = await request.json()
        state["calls"].append(("create", body))
        state["scenarios"].append({"id": "sc-new", "name": body["name"], "triggers": [
            {"type": "scenario.trigger.voice", "value": body["triggers"][0]["trigger"]["value"]}]})
        return web.json_response({"status": "ok", "scenario_id": "sc-new"})

    async def update(request):
        if state["forbid_once"]:
            state["forbid_once"] = False
            state["csrf_n"] += 1
            return web.json_response({}, status=403)
        if request.headers.get("x-csrf-token") != csrf():
            return web.json_response({}, status=403)
        body = await request.json()
        text = body["steps"][0]["parameters"]["items"][0]["value"]["capabilities"][0]["state"]["value"]["text"]
        sid = request.match_info["sid"]
        if sid not in {sc["id"] for sc in state["scenarios"]}:
            return web.json_response({"status": "error", "code": "SCENARIO_NOT_FOUND"})
        state["calls"].append(("put", sid, text))
        await asyncio.sleep(state["put_delay"])
        if state["put_error"]:
            return web.json_response({"status": "error", "code": state["put_error"]})
        return web.json_response({"status": "ok"})

    async def run(request):
        state["calls"].append(("run", request.match_info["sid"]))
        if state["run_error"]:
            return web.json_response({"status": "error", "code": state["run_error"]})
        return web.json_response({"status": "ok"})

    app = web.Application()
    app.router.add_post("/1/bundle/auth/x_token/", x_token)
    app.router.add_get("/auth/session/", auth_session)
    async def finish(request):
        return web.Response(text="ok")

    app.router.add_get("/auth/finish", finish)
    app.router.add_get("/iot", iot_page)
    app.router.add_get("/m/v3/user/devices", devices)
    app.router.add_get("/m/user/scenarios", scenarios)
    app.router.add_post("/m/v4/user/scenarios", create)
    app.router.add_put("/m/v4/user/scenarios/{sid}", update)
    app.router.add_post("/m/user/scenarios/{sid}/actions", run)
    server = await aiohttp_server(app)
    state["base"] = str(server.make_url("")).rstrip("/")
    return state


@pytest.fixture
async def speaker(yandex):
    async with ClientSession() as http:
        base = yandex["base"]
        yield QuasarSpeaker(http, TOKEN, mobileproxy=base, yandex=base, iot=base)


def kinds(state):
    return [c[0] for c in state["calls"]]


def test_encode():
    assert encode("0a-") == "окы"  # 0->о, a (index 10)->к, - ->ы


async def test_say_updates_then_runs_scenario(speaker, yandex):
    assert await speaker.start() == Speech.OK
    assert await speaker.say(DEVICE, "Привет") == Speech.OK
    assert kinds(yandex) == ["devices", "scenarios", "put", "run"]
    assert yandex["calls"][2] == ("put", "sc-1", "Привет")
    assert yandex["calls"][3] == ("run", "sc-1")


async def test_say_logs_in_by_itself_when_not_started(speaker, yandex):
    assert await speaker.say(DEVICE, "Привет") == Speech.OK
    assert yandex["logins"] == 1


async def test_creates_scenario_when_missing(speaker, yandex):
    yandex["scenarios"] = []
    await speaker.start()
    assert await speaker.say(DEVICE, "Привет") == Speech.OK
    [created] = [c[1] for c in yandex["calls"] if c[0] == "create"]
    assert created["name"] == "Гермес озвучка"
    assert created["triggers"][0]["trigger"] == {"type": "scenario.trigger.voice", "value": encode(DEVICE)}
    assert ("put", "sc-new", "Привет") in yandex["calls"]


async def test_scenario_id_is_cached(speaker, yandex):
    await speaker.start()
    await speaker.say(DEVICE, "раз")
    await speaker.say(DEVICE, "два")
    assert kinds(yandex).count("scenarios") == 1


async def test_offline_speaker(speaker, yandex):
    yandex["device_state"] = "offline"
    await speaker.start()
    assert await speaker.say(DEVICE, "Привет") == Speech.OFFLINE
    assert "put" not in kinds(yandex)


async def test_unknown_speaker_is_offline(speaker, yandex):
    await speaker.start()
    assert await speaker.say("00000000-0000-0000-0000-000000000000", "Привет") == Speech.OFFLINE


async def test_401_relogs_once(speaker, yandex):
    await speaker.start()
    yandex["unauth_once"] = True
    assert await speaker.say(DEVICE, "Привет") == Speech.OK
    assert yandex["logins"] == 2


async def test_lost_login_is_auth_lost(speaker, yandex):
    yandex["login_ok"] = False
    assert await speaker.start() == Speech.AUTH_LOST
    assert await speaker.say(DEVICE, "Привет") == Speech.AUTH_LOST
    assert "put" not in kinds(yandex)


async def test_lost_login_after_401_is_auth_lost(speaker, yandex):
    await speaker.start()
    yandex["unauth_once"] = True
    yandex["login_ok"] = False
    assert await speaker.say(DEVICE, "Привет") == Speech.AUTH_LOST


async def test_403_refreshes_csrf_once(speaker, yandex):
    await speaker.start()
    yandex["forbid_once"] = True
    assert await speaker.say(DEVICE, "Привет") == Speech.OK
    assert ("put", "sc-1", "Привет") in yandex["calls"]


async def test_status_error_in_200_is_rejected(speaker, yandex, caplog):
    caplog.set_level(logging.INFO)
    yandex["put_error"] = "QUASAR_SERVER_ACTION_LENGTH_ERROR"
    await speaker.start()
    assert await speaker.say(DEVICE, "Привет") == Speech.REJECTED
    assert "run" not in kinds(yandex)
    assert "QUASAR_SERVER_ACTION_LENGTH_ERROR" in caplog.text


async def test_unreachable_yandex_is_error():
    async with ClientSession() as http:
        dead = "http://127.0.0.1:1"
        s = QuasarSpeaker(http, TOKEN, mobileproxy=dead, yandex=dead, iot=dead)
        assert await s.say(DEVICE, "Привет") == Speech.ERROR


async def test_concurrent_says_do_not_interleave(speaker, yandex):
    await speaker.start()
    results = await asyncio.gather(speaker.say(DEVICE, "первый"), speaker.say(DEVICE, "второй"))
    assert results == [Speech.OK, Speech.OK]
    steps = [c for c in yandex["calls"] if c[0] in ("put", "run")]
    assert [c[0] for c in steps] == ["put", "run", "put", "run"]


async def test_token_never_logged(speaker, yandex, caplog):
    caplog.set_level(logging.DEBUG)
    yandex["unauth_once"] = True
    await speaker.start()
    await speaker.say(DEVICE, "Привет")
    assert TOKEN not in caplog.text
    assert "csrf-1" not in caplog.text and "csrf-2" not in caplog.text


async def test_session_cookies_go_back_unquoted(yandex):
    yandex["check_cookie"] = True
    async with http_session(unsafe=True) as http:  # unsafe: the fake server is an IP address
        base = yandex["base"]
        s = QuasarSpeaker(http, TOKEN, mobileproxy=base, yandex=base, iot=base)
        assert await s.say(DEVICE, "Привет") == Speech.OK


async def test_unexpected_login_shape_is_error_not_crash(speaker, yandex):
    yandex["bare_ok"] = True  # status ok but no passport_host / track_id
    assert await speaker.start() == Speech.ERROR
    assert await speaker.say(DEVICE, "Привет") == Speech.ERROR


async def test_recreated_scenario_is_found_again_in_the_same_call(speaker, yandex):
    await speaker.start()
    assert await speaker.say(DEVICE, "раз") == Speech.OK  # caches sc-1
    yandex["scenarios"] = [{"id": "sc-2", "name": SCENARIO_NAME,
                            "triggers": [{"type": "scenario.trigger.voice", "value": encode(DEVICE)}]}]
    assert await speaker.say(DEVICE, "два") == Speech.OK
    assert ("put", "sc-2", "два") in yandex["calls"] and ("run", "sc-2") in yandex["calls"]


async def test_run_step_status_error_is_rejected(speaker, yandex):
    yandex["run_error"] = "DEVICE_UNREACHABLE"
    await speaker.start()
    assert await speaker.say(DEVICE, "Привет") == Speech.REJECTED


async def test_say_has_an_overall_deadline(speaker, yandex, monkeypatch):
    monkeypatch.setattr("alice_bridge.quasar.SAY_DEADLINE_S", 0.3)
    yandex["put_delay"] = 1
    await speaker.start()
    loop = asyncio.get_running_loop()
    started = loop.time()
    assert await speaker.say(DEVICE, "Привет") == Speech.ERROR
    assert loop.time() - started < 0.8
