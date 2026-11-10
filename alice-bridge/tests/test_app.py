import asyncio
import hashlib
import hmac
import json
import logging
import time

import pytest
from aiohttp import web

from alice_bridge.quasar import Speech
from alice_bridge.app import ASK, FAILED, HELP, Config, configure_logging, make_app, parse_stations

PATH_SECRET = "path-secret-value"
WEBHOOK_SECRET = "webhook-secret-value"
SKILL = "skill-1"
USER = "user-1"


def alice(command="купить молоко", *, skill=SKILL, user=USER, new=True,
          utterance=None, session_id="s1", message_id=0, application_id="app-1",
          client_id="ru.yandex.quasar.app/1.0"):
    return {
        "meta": {"locale": "ru-RU", "client_id": client_id},
        "session": {"new": new, "session_id": session_id, "message_id": message_id,
                    "skill_id": skill, "user": {"user_id": user},
                    "application": {"application_id": application_id}},
        "request": {"command": command,
                    "original_utterance": command if utterance is None else utterance,
                    "type": "SimpleUtterance"},
        "version": "1.0",
    }


@pytest.fixture
def hermes():
    """A stand-in for hermes' webhook platform that records what it got."""
    state = {"calls": [], "status": 202, "delay": 0}

    async def handler(request):
        state["calls"].append({"body": await request.read(), "headers": dict(request.headers)})
        await asyncio.sleep(state["delay"])
        return web.json_response({"status": "accepted"}, status=state["status"])

    app = web.Application()
    app.router.add_post("/webhooks/alice", handler)
    state["app"] = app
    return state


@pytest.fixture
async def client(aiohttp_client, aiohttp_server, hermes):
    server = await aiohttp_server(hermes["app"])

    async def make(speaker_factory=None, **overrides):
        cfg = dict(path_secret=PATH_SECRET, webhook_secret=WEBHOOK_SECRET, skill_id=SKILL,
                   allowed_user_ids=frozenset(),
                   hermes_url=str(server.make_url("/webhooks/alice")))
        cfg.update(overrides)
        return await aiohttp_client(make_app(Config(**cfg), speaker_factory=speaker_factory))
    return make


async def post(c, body, secret=PATH_SECRET):
    return await c.post(f"/alice/{secret}", json=body)


async def test_forwards_signed_phrase_and_echoes_it(client, hermes):
    c = await client()
    resp = await post(c, alice())
    assert resp.status == 200
    data = await resp.json()
    # The skill stays open: the Station listens again and the next command
    # needs neither «Алиса» nor the skill's name.
    assert data["response"] == {"text": "Передал: купить молоко", "end_session": False}
    [call] = hermes["calls"]
    assert json.loads(call["body"]) == {"text": "купить молоко", "source": "alice", "speaker": ""}
    ts = call["headers"]["X-Webhook-Timestamp"]
    assert abs(int(ts) - time.time()) < 5
    expected = hmac.new(WEBHOOK_SECRET.encode(), ts.encode() + b"." + call["body"], hashlib.sha256).hexdigest()
    assert call["headers"]["X-Webhook-Signature-V2"] == expected
    assert "X-Webhook-Signature" not in call["headers"]  # no replayable body-only V1
    assert call["headers"]["X-Request-ID"] == "alice:s1:0"


async def test_duplicate_delivery_still_counts_as_passed(client, hermes):
    hermes["status"] = 200
    c = await client()
    data = await (await post(c, alice())).json()
    assert data["response"]["text"].startswith("Передал")


async def test_command_wins_over_utterance_with_activation_phrase(client, hermes):
    c = await client()
    await post(c, alice("купить молоко", utterance="попроси агента гермеса купить молоко"))
    assert json.loads(hermes["calls"][0]["body"])["text"] == "купить молоко"


@pytest.mark.parametrize("status", [401, 429, 500])
async def test_hermes_error_is_reported(client, hermes, status):
    hermes["status"] = status
    c = await client()
    data = await (await post(c, alice())).json()
    assert data["response"] == {"text": FAILED, "end_session": True}


async def test_hermes_timeout_is_reported_in_time(client, hermes, monkeypatch):
    monkeypatch.setattr("alice_bridge.app.FORWARD_TIMEOUT_S", 0.2)
    hermes["delay"] = 1
    c = await client()
    loop = asyncio.get_running_loop()
    started = loop.time()
    data = await (await post(c, alice())).json()
    assert data["response"] == {"text": FAILED, "end_session": True}
    assert loop.time() - started < 0.9


async def test_hermes_unreachable_is_reported(client):
    c = await client(hermes_url="http://127.0.0.1:1/webhooks/alice")
    data = await (await post(c, alice())).json()
    assert data["response"] == {"text": FAILED, "end_session": True}


async def test_wrong_path_secret_is_404(client, hermes):
    c = await client()
    assert (await post(c, alice(), secret="nope")).status == 404
    assert hermes["calls"] == []


async def test_refused_requests_are_logged_without_the_path(client, caplog):
    caplog.set_level(logging.WARNING)
    c = await client()
    await c.post("/alice/", json=alice())
    await post(c, alice(), secret="wrong-secret")
    assert caplog.text.count("refused POST request: HTTP 404") == 2
    assert "wrong-secret" not in caplog.text


async def test_unset_path_secret_rejects_everything(client, hermes):
    c = await client(path_secret="")
    assert (await post(c, alice(), secret="")).status == 404
    assert (await post(c, alice(), secret="x")).status == 404


async def test_wrong_skill_is_403(client, hermes):
    c = await client()
    assert (await post(c, alice(skill="other"))).status == 403
    assert hermes["calls"] == []


@pytest.mark.parametrize("missing", ["skill_id", "webhook_secret"])
async def test_unset_config_fails_closed(client, hermes, missing):
    c = await client(**{missing: ""})
    assert (await post(c, alice())).status == 403
    assert hermes["calls"] == []


async def test_user_allowlist(client, hermes):
    c = await client(allowed_user_ids=frozenset({USER}))
    assert (await post(c, alice(user="stranger"))).status == 403
    assert (await post(c, alice())).status == 200
    assert len(hermes["calls"]) == 1


async def test_bad_json_is_400(client):
    c = await client()
    resp = await c.post(f"/alice/{PATH_SECRET}", data=b"{nope")
    assert resp.status == 400


async def test_ping_is_answered_locally(client, hermes):
    c = await client()
    data = await (await post(c, alice("ping"))).json()
    assert data["response"]["text"] == "pong"
    assert hermes["calls"] == []


async def test_empty_launch_asks_and_keeps_session(client, hermes):
    c = await client()
    data = await (await post(c, alice(""))).json()
    assert data["response"] == {"text": ASK, "end_session": False}
    assert hermes["calls"] == []


async def test_help_is_answered_locally(client, hermes):
    c = await client()
    data = await (await post(c, alice("что ты умеешь"))).json()
    assert data["response"]["text"] == HELP
    assert hermes["calls"] == []


async def test_interrupt_ends_without_forwarding(client, hermes):
    c = await client()
    data = await (await post(c, alice("on_interrupt"))).json()
    assert data["response"]["end_session"] is True
    assert hermes["calls"] == []


async def test_health(client):
    c = await client()
    assert (await c.get("/health")).status == 200


async def test_secrets_and_utterance_never_logged_at_info(client, hermes, caplog):
    configure_logging()
    caplog.set_level(logging.INFO)
    c = await client()
    await post(c, alice())
    await post(c, alice(skill="other"))
    await post(c, alice(), secret="wrong")
    text = caplog.text
    for secret in (PATH_SECRET, WEBHOOK_SECRET, "купить молоко"):
        assert secret not in text


async def test_station_origin_is_forwarded_as_speaker(client, hermes):
    c = await client(stations={"app-station": "dev-1"})
    await post(c, alice(application_id="app-station"))
    assert json.loads(hermes["calls"][0]["body"])["speaker"] == "dev-1"


async def test_unmapped_origin_has_empty_speaker_and_logs_ids(client, hermes, caplog):
    caplog.set_level(logging.INFO)
    c = await client(stations={"app-station": "dev-1"})
    await post(c, alice(application_id="app-phone", client_id="ru.yandex.searchplugin/1.0"))
    assert json.loads(hermes["calls"][0]["body"])["speaker"] == ""
    assert "app-phone" in caplog.text
    assert "ru.yandex.searchplugin/1.0" in caplog.text


def test_stations_env_parsing():
    assert parse_stations("a=d1, b=d2,") == {"a": "d1", "b": "d2"}
    assert parse_stations("") == {}


class FakeSpeaker:
    def __init__(self, result=Speech.OK):
        self.said, self.started, self.result = [], 0, result

    async def start(self):
        self.started += 1
        return Speech.OK

    async def say(self, device_id, text):
        self.said.append((device_id, text))
        return self.result


@pytest.fixture
def fake_speaker():
    speaker = FakeSpeaker()
    tokens = []

    def factory(http, x_token):
        tokens.append(x_token)
        return speaker
    speaker.tokens = tokens
    speaker.factory = factory
    return speaker


def speak_headers(raw, *, secret=WEBHOOK_SECRET, ts=None, signature=None):
    ts = str(int(time.time())) if ts is None else ts
    sig = signature if signature is not None else hmac.new(
        secret.encode(), b"speak." + ts.encode() + b"." + raw, hashlib.sha256).hexdigest()
    return {"X-Speak-Timestamp": ts, "X-Speak-Signature": sig, "Content-Type": "application/json"}


async def speak(c, body, **kw):
    raw = json.dumps(body, ensure_ascii=False).encode()
    return await c.post("/speak", data=raw, headers=speak_headers(raw, **kw))


async def test_speak_requires_signature(client, fake_speaker):
    c = await client(speaker_factory=fake_speaker.factory, x_token="tok")
    assert (await speak(c, {"speaker": "dev-1", "text": "Привет"}, secret="wrong")).status == 401
    assert (await speak(c, {"speaker": "dev-1", "text": "Привет"}, signature="")).status == 401
    assert fake_speaker.said == []


async def test_speak_passes_text_to_speaker(client, fake_speaker):
    c = await client(speaker_factory=fake_speaker.factory, x_token="tok")
    resp = await speak(c, {"speaker": "dev-1", "text": "Привет"})
    assert resp.status == 200
    assert await resp.json() == {"result": "ok"}
    assert fake_speaker.said == [("dev-1", "Привет")]
    assert fake_speaker.tokens == ["tok"] and fake_speaker.started == 1


async def test_speak_queues_a_phrase_while_the_station_speaks(client, fake_speaker):
    c = await client(speaker_factory=fake_speaker.factory, x_token="tok")
    await speak(c, {"speaker": "dev-1", "text": "Первый"})
    assert await (await speak(c, {"speaker": "dev-1", "text": "Второй"})).json() == {"result": "queued"}
    assert fake_speaker.said == [("dev-1", "Первый")]


async def test_speak_reports_speaker_result(client, fake_speaker):
    fake_speaker.result = Speech.AUTH_LOST
    c = await client(speaker_factory=fake_speaker.factory, x_token="tok")
    assert await (await speak(c, {"speaker": "dev-1", "text": "Привет"})).json() == {"result": "auth_lost"}


@pytest.mark.parametrize("body", [{"speaker": "dev-1", "text": "я" * 101}, {"speaker": "", "text": "Привет"},
                                  {"speaker": "dev-1", "text": ""}, ["not", "a", "dict"]])
async def test_speak_rejects_bad_body(client, fake_speaker, body):
    c = await client(speaker_factory=fake_speaker.factory, x_token="tok")
    assert (await speak(c, body)).status == 400
    assert fake_speaker.said == []


async def test_speak_accepts_100_chars(client, fake_speaker):
    c = await client(speaker_factory=fake_speaker.factory, x_token="tok")
    assert (await speak(c, {"speaker": "dev-1", "text": "я" * 100})).status == 200


async def test_speak_without_token_is_not_configured(client, fake_speaker):
    c = await client(speaker_factory=fake_speaker.factory)
    assert await (await speak(c, {"speaker": "dev-1", "text": "Привет"})).json() == {"result": "not_configured"}
    assert fake_speaker.tokens == []


async def test_speak_result_is_logged_without_text(client, fake_speaker, caplog):
    configure_logging()
    caplog.set_level(logging.INFO)
    c = await client(speaker_factory=fake_speaker.factory, x_token="tok")
    await speak(c, {"speaker": "dev-1", "text": "секретная фраза"})
    assert "spoke on dev-1: ok" in caplog.text
    assert "секретная фраза" not in caplog.text and "tok" not in caplog.text.replace("token", "")


async def test_speak_rejects_a_forward_style_signature(client, fake_speaker):
    c = await client(speaker_factory=fake_speaker.factory, x_token="tok")
    raw = json.dumps({"speaker": "dev-1", "text": "Привет"}).encode()
    ts = str(int(time.time()))
    v2 = hmac.new(WEBHOOK_SECRET.encode(), ts.encode() + b"." + raw, hashlib.sha256).hexdigest()
    resp = await c.post("/speak", data=raw, headers={"X-Speak-Timestamp": ts, "X-Speak-Signature": v2})
    assert resp.status == 401
    v1 = hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    assert (await c.post("/speak", data=raw, headers={"X-Webhook-Signature": v1})).status == 401
    assert fake_speaker.said == []


async def test_speak_rejects_a_stale_timestamp(client, fake_speaker):
    c = await client(speaker_factory=fake_speaker.factory, x_token="tok")
    old = str(int(time.time()) - 301)
    assert (await speak(c, {"speaker": "dev-1", "text": "Привет"}, ts=old)).status == 401
    assert (await speak(c, {"speaker": "dev-1", "text": "Привет"}, ts="soon")).status == 401
    assert fake_speaker.said == []


async def test_speak_rejects_a_replayed_request(client, fake_speaker):
    c = await client(speaker_factory=fake_speaker.factory, x_token="tok")
    raw = json.dumps({"speaker": "dev-1", "text": "Привет"}).encode()
    headers = speak_headers(raw)
    assert (await c.post("/speak", data=raw, headers=headers)).status == 200
    assert (await c.post("/speak", data=raw, headers=headers)).status == 401
    assert fake_speaker.said == [("dev-1", "Привет")]


async def test_speak_non_ascii_signature_is_401(client, fake_speaker):
    c = await client(speaker_factory=fake_speaker.factory, x_token="tok")
    raw = json.dumps({"speaker": "dev-1", "text": "Привет"}).encode()
    headers = speak_headers(raw, signature="подпись")
    headers = {k: v.encode("utf-8").decode("latin-1") for k, v in headers.items()}
    assert (await c.post("/speak", data=raw, headers=headers)).status == 401
