import asyncio
import hashlib
import hmac
import json
import logging

import pytest
from aiohttp import web

from alice_bridge.app import ASK, FAILED, HELP, Config, configure_logging, make_app

PATH_SECRET = "path-secret-value"
WEBHOOK_SECRET = "webhook-secret-value"
SKILL = "skill-1"
USER = "user-1"


def alice(command="купить молоко", *, skill=SKILL, user=USER, new=True,
          utterance=None, session_id="s1", message_id=0):
    return {
        "meta": {"locale": "ru-RU"},
        "session": {"new": new, "session_id": session_id, "message_id": message_id,
                    "skill_id": skill, "user": {"user_id": user},
                    "application": {"application_id": "app-1"}},
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

    async def make(**overrides):
        cfg = dict(path_secret=PATH_SECRET, webhook_secret=WEBHOOK_SECRET, skill_id=SKILL,
                   allowed_user_ids=frozenset(),
                   hermes_url=str(server.make_url("/webhooks/alice")))
        cfg.update(overrides)
        return await aiohttp_client(make_app(Config(**cfg)))
    return make


async def post(c, body, secret=PATH_SECRET):
    return await c.post(f"/alice/{secret}", json=body)


async def test_forwards_signed_phrase_and_echoes_it(client, hermes):
    c = await client()
    resp = await post(c, alice())
    assert resp.status == 200
    data = await resp.json()
    assert data["response"] == {"text": "Передал: купить молоко", "end_session": True}
    [call] = hermes["calls"]
    assert json.loads(call["body"]) == {"text": "купить молоко", "source": "alice"}
    expected = hmac.new(WEBHOOK_SECRET.encode(), call["body"], hashlib.sha256).hexdigest()
    assert call["headers"]["X-Webhook-Signature"] == expected
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
    assert data["response"]["text"] == FAILED


async def test_hermes_timeout_is_reported_in_time(client, hermes, monkeypatch):
    monkeypatch.setattr("alice_bridge.app.FORWARD_TIMEOUT_S", 0.2)
    hermes["delay"] = 1
    c = await client()
    loop = asyncio.get_running_loop()
    started = loop.time()
    data = await (await post(c, alice())).json()
    assert data["response"]["text"] == FAILED
    assert loop.time() - started < 0.9


async def test_hermes_unreachable_is_reported(client):
    c = await client(hermes_url="http://127.0.0.1:1/webhooks/alice")
    data = await (await post(c, alice())).json()
    assert data["response"]["text"] == FAILED


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
