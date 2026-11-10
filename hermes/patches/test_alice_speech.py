"""Tests for the runtime module that speaks a voice turn's 🔊 line on the Station.

It runs inside hermes' webhook delivery, so it must never raise, never change
a route that did not opt in, and always leave Telegram a clean answer.
"""
import asyncio
import hashlib
import hmac
import json
import time

import pytest

import alice_speech as speech

SECRET = "webhook-secret-value"
ANSWER = "🎙 какие задачи на завтра\nНа завтра девять задач:\n1. Ремонт\n2. tcpdump"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("ALICE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("ALICE_SPEAK_URL", "http://bridge.test/speak")


class Post:
    def __init__(self, result=None, exc=None):
        self.calls, self.result, self.exc = [], result if result is not None else {"result": "ok"}, exc

    async def __call__(self, url, body, headers, timeout_s):
        self.calls.append({"url": url, "body": body, "headers": headers, "timeout_s": timeout_s})
        if self.exc:
            raise self.exc
        return self.result

    @property
    def spoken(self):
        return [json.loads(c["body"])["text"] for c in self.calls]


def delivery(speaker="dev-1"):
    return {"deliver": "telegram", "deliver_extra": {"speak_on": speaker}, "route": "alice"}


def run(content, deliv, post):
    return asyncio.run(speech.speak_then_strip(content, deliv, post=post))


def test_last_spoken_line_is_said_and_all_are_stripped():
    post = Post()
    content = f"{ANSWER}\n🔊 черновик\n  🔊 На завтра девять задач, остальное в Телеграме."
    out = run(content, delivery(), post)
    assert post.spoken == ["На завтра девять задач, остальное в Телеграме."]
    assert "🔊" not in out
    assert out == ANSWER


def test_missing_line_speaks_fallback():
    post = Post()
    assert run(ANSWER, delivery(), post) == ANSWER
    assert post.spoken == ["Ответ в Телеграме."]


def test_empty_spoken_line_speaks_fallback():
    post = Post()
    run(ANSWER + "\n🔊   ", delivery(), post)
    assert post.spoken == ["Ответ в Телеграме."]


def test_for_ear_strips_markup_and_urls():
    assert speech.for_ear("**Готово**, ссылка https://x.y/z 🎉 [док](https://d)") == "Готово, ссылка док"


def test_for_ear_keeps_ordinary_punctuation_and_digits():
    assert speech.for_ear("Задача стоит на 7 ноября, в 10:30 — да?") == "Задача стоит на 7 ноября, в 10:30 — да?"


def test_for_ear_cuts_at_word_boundary():
    text = " ".join(["слово"] * 24)  # 143 chars
    out = speech.for_ear(text)
    assert len(out) <= 100
    assert out.split() == ["слово"] * len(out.split())


def test_long_spoken_line_is_cut_before_sending():
    post = Post()
    run(ANSWER + "\n🔊 " + "очень " * 30, delivery(), post)
    assert len(post.spoken[0]) <= 100


def test_failure_appends_mute_line():
    out = run(ANSWER + "\n🔊 Готово.", delivery(), Post({"result": "offline"}))
    assert out == ANSWER + "\n\n🔇 Станция не озвучила ответ"


def test_queued_phrase_counts_as_spoken():
    # alice-bridge queues a phrase while the Station is still speaking.
    out = run(ANSWER + "\n🔊 Готово.", delivery(), Post({"result": "queued"}))
    assert out == ANSWER


def test_auth_lost_line():
    out = run(ANSWER + "\n🔊 Готово.", delivery(), Post({"result": "auth_lost"}))
    assert out == ANSWER + "\n\n🔇 Станция не озвучила ответ: нужен повторный вход в Яндекс"


async def answers_nothing(url, body, headers, timeout_s):
    return None


@pytest.mark.parametrize("post", [Post(exc=RuntimeError("boom")), Post(exc=asyncio.TimeoutError()),
                                  Post(result={}), answers_nothing])
def test_post_failure_still_returns_content(post):
    out = run(ANSWER + "\n🔊 Готово.", delivery(), post)
    assert out == ANSWER + "\n\n🔇 Станция не озвучила ответ"


def test_not_opted_in_is_untouched():
    post = Post()
    content = ANSWER + "\n🔊 Готово."
    assert run(content, {"deliver": "telegram", "deliver_extra": {}}, post) == content
    assert run(content, {"deliver": "telegram"}, post) == content
    assert post.calls == []


@pytest.mark.parametrize("speaker", ["", "{speaker}", "  "])
def test_placeholder_or_empty_speaker_strips_without_speaking(speaker):
    post = Post()
    assert run(ANSWER + "\n🔊 Готово.", delivery(speaker), post) == ANSWER
    assert post.calls == []


def test_request_is_signed_and_bounded():
    post = Post()
    run(ANSWER + "\n🔊 Готово.", delivery(), post)
    [call] = post.calls
    assert call["url"] == "http://bridge.test/speak"
    assert json.loads(call["body"]) == {"speaker": "dev-1", "text": "Готово."}
    ts = call["headers"]["X-Speak-Timestamp"]
    assert abs(int(ts) - time.time()) < 5
    expected = hmac.new(SECRET.encode(), b"speak." + ts.encode() + b"." + call["body"], hashlib.sha256).hexdigest()
    assert call["headers"]["X-Speak-Signature"] == expected
    assert "X-Webhook-Signature" not in call["headers"]
    assert call["timeout_s"] == 5


def test_no_secret_means_no_call(monkeypatch):
    monkeypatch.delenv("ALICE_WEBHOOK_SECRET")
    post = Post()
    out = run(ANSWER + "\n🔊 Готово.", delivery(), post)
    assert post.calls == []
    assert out == ANSWER + "\n\n🔇 Станция не озвучила ответ"


def test_extract_spoken():
    assert speech.extract_spoken("a\n🔊 b\nc") == ("a\nc", "b")
    assert speech.extract_spoken("a") == ("a", None)


def test_interim_send_strips_without_speaking():
    post = Post()
    out = asyncio.run(speech.speak_then_strip("⚠️ I seem to be stuck\n🔊 x", delivery(), post=post,
                                              metadata={"_interim_send": True}))
    assert out == "⚠️ I seem to be stuck"
    assert post.calls == []


@pytest.mark.parametrize("line", ["**🔊 Готово.**", "- 🔊 Готово.", "> 🔊 Готово.", "_🔊 Готово._",
                                  "«🔊 Готово.»"])
def test_spoken_line_wrapped_in_markup_is_found(line):
    post = Post()
    out = run(ANSWER + "\n" + line, delivery(), post)
    assert out == ANSWER
    assert post.spoken == ["Готово."]


def test_for_ear_drops_bare_domains_but_keeps_times():
    assert speech.for_ear("Ссылка t.me/jawello и docs.google.com/d/1, встреча в 10.30") == \
        "Ссылка и, встреча в 10.30"


@pytest.mark.parametrize("notice", ["⚠️ I seem to be stuck", "⚠️ Context compressed"])
def test_status_notice_is_neither_spoken_nor_marked(notice):
    post = Post()
    assert run(notice, delivery(), post) == notice
    assert post.calls == []


def test_answer_with_mic_line_but_no_spoken_line_speaks_fallback():
    post = Post()
    run("  🎙 что качается\nНичего не качается.", delivery(), post)
    assert post.spoken == ["Ответ в Телеграме."]


@pytest.mark.parametrize("text,ear", [
    ("На улице 20°C", "На улице 20 градусов"),
    ("Стоит 5 €, доставка $30", "Стоит 5 евро, доставка 30 долларов"),
    ("Итого 1500 ₽ & чаевые", "Итого 1500 рублей и чаевые"),
    ("<b>Готово</b>", "Готово"),
])
def test_for_ear_says_symbols_as_words(text, ear):
    assert speech.for_ear(text) == ear


def test_default_poster_posts_and_reads_the_result():
    pytest.importorskip("aiohttp")  # hermes has it; CI installs it for this test
    from aiohttp import web

    seen = {}

    async def handler(request):
        seen["body"] = await request.read()
        seen["headers"] = dict(request.headers)
        return web.json_response({"result": "ok"})

    async def scenario():
        app = web.Application()
        app.router.add_post("/speak", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            ok = await speech._aiohttp_post(f"http://127.0.0.1:{port}/speak", b'{"a": 1}',
                                            {"X-Speak-Signature": "s"}, 2)
            missing = await speech._aiohttp_post(f"http://127.0.0.1:{port}/nope", b"{}", {}, 2)
        finally:
            await runner.cleanup()
        return ok, missing

    ok, missing = asyncio.run(scenario())
    assert ok == {"result": "ok"} and missing is None
    assert seen["body"] == b'{"a": 1}' and seen["headers"]["X-Speak-Signature"] == "s"


@pytest.mark.parametrize("notice", ["👁️ Hindsight — recalled 22 memories", "🧠 Hindsight — recalled 1 memory",
                                    "🧠 Honcho — recalled relevant memory"])
def test_recall_notice_of_a_voice_turn_is_not_delivered(notice):
    post = Post()
    assert run(notice, delivery(), post) is None
    assert post.calls == []


def test_recall_notice_elsewhere_is_untouched():
    notice = "👁️ Hindsight — recalled 22 memories"
    assert run(notice, {"deliver": "telegram", "deliver_extra": {}}, Post()) == notice


def test_answer_mentioning_recall_is_still_delivered():
    post = Post()
    out = run("🎙 что помнишь\nHindsight — recalled 3 memories о ремонте.\n🔊 Помню три вещи.", delivery(), post)
    assert out.startswith("🎙 что помнишь") and post.spoken == ["Помню три вещи."]
