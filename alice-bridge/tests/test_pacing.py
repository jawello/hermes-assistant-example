import asyncio
import logging

import pytest

from alice_bridge.pacing import MAX_WAIT_S, NEAR_S, SIGNAL, Pacer, duration
from alice_bridge.quasar import Speech


class Clock:
    """Fake time: sleeping moves it forward and lets other tasks run."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.now += max(seconds, 0)
        await asyncio.sleep(0)


class Speaker:
    def __init__(self, clock, results=None):
        self.clock, self.said, self.results = clock, [], list(results or [])

    async def start(self):
        return Speech.OK

    async def say(self, device_id, text):
        self.said.append((round(self.clock(), 2), device_id, text))
        return self.results.pop(0) if self.results else Speech.OK


@pytest.fixture
def clock():
    return Clock()


async def settle(pacer):
    await asyncio.gather(*pacer.pending())


async def test_free_station_speaks_at_once(clock):
    speaker = Speaker(clock)
    pacer = Pacer(speaker, clock=clock, sleep=clock.sleep)
    assert await pacer.say("dev", "Готово.") is Speech.OK
    assert speaker.said == [(1000.0, "dev", "Готово.")]


async def test_answer_while_speaking_waits_then_signal_then_answer(clock):
    speaker = Speaker(clock)
    pacer = Pacer(speaker, clock=clock, sleep=clock.sleep)
    first = "Первый ответ про задачи на завтра."
    await pacer.say("dev", first)
    clock.now += 1
    assert await pacer.say("dev", "Второй.") is Speech.QUEUED
    await settle(pacer)
    (_, _, t1), (at_signal, _, t2), (at_answer, _, t3) = speaker.said
    assert (t1, t2, t3) == (first, SIGNAL, "Второй.")
    assert at_signal >= 1000.0 + duration(first)   # never cuts the first answer
    assert at_answer - at_signal >= duration(SIGNAL)  # never cuts the signal


async def test_answer_soon_after_the_last_one_gets_the_signal(clock):
    speaker = Speaker(clock)
    pacer = Pacer(speaker, clock=clock, sleep=clock.sleep)
    await pacer.say("dev", "Раз.")
    clock.now += duration("Раз.") + NEAR_S - 1
    assert await pacer.say("dev", "Два.") is Speech.QUEUED
    await settle(pacer)
    assert [t for _, _, t in speaker.said] == ["Раз.", SIGNAL, "Два."]


async def test_answer_long_after_the_last_one_has_no_signal(clock):
    speaker = Speaker(clock)
    pacer = Pacer(speaker, clock=clock, sleep=clock.sleep)
    await pacer.say("dev", "Раз.")
    clock.now += duration("Раз.") + NEAR_S + 1
    assert await pacer.say("dev", "Два.") is Speech.OK
    assert [t for _, _, t in speaker.said] == ["Раз.", "Два."]


async def test_three_answers_play_in_order_without_cutting(clock):
    speaker = Speaker(clock)
    pacer = Pacer(speaker, clock=clock, sleep=clock.sleep)
    await pacer.say("dev", "Раз, длинный первый ответ.")
    assert await pacer.say("dev", "Два, второй ответ.") is Speech.QUEUED
    assert await pacer.say("dev", "Три.") is Speech.QUEUED
    await settle(pacer)
    texts = [t for _, _, t in speaker.said]
    assert texts == ["Раз, длинный первый ответ.", SIGNAL, "Два, второй ответ.", SIGNAL, "Три."]
    ends = [at + duration(t) for at, _, t in speaker.said]
    starts = [at for at, _, _ in speaker.said]
    assert all(start >= end for end, start in zip(ends, starts[1:]))


async def test_stations_do_not_wait_for_each_other(clock):
    speaker = Speaker(clock)
    pacer = Pacer(speaker, clock=clock, sleep=clock.sleep)
    await pacer.say("dev-1", "Раз.")
    assert await pacer.say("dev-2", "Два.") is Speech.OK


async def test_failed_direct_phrase_does_not_hold_the_station(clock):
    speaker = Speaker(clock, results=[Speech.ERROR])
    pacer = Pacer(speaker, clock=clock, sleep=clock.sleep)
    assert await pacer.say("dev", "Раз.") is Speech.ERROR
    assert await pacer.say("dev", "Два.") is Speech.OK


async def test_concurrent_direct_phrases_do_not_cut_each_other(clock):
    gate = asyncio.Event()

    class Slow(Speaker):
        async def say(self, device_id, text):
            await gate.wait()
            return await super().say(device_id, text)

    speaker = Slow(clock)
    pacer = Pacer(speaker, clock=clock, sleep=clock.sleep)
    first = asyncio.create_task(pacer.say("dev", "Раз."))
    await asyncio.sleep(0)
    assert await pacer.say("dev", "Два.") is Speech.QUEUED
    gate.set()
    assert await first is Speech.OK
    await settle(pacer)
    assert [t for _, _, t in speaker.said] == ["Раз.", SIGNAL, "Два."]


async def test_stale_queued_answer_is_dropped(clock, caplog):
    speaker = Speaker(clock)
    pacer = Pacer(speaker, clock=clock, sleep=clock.sleep)
    long = "а" * 100
    await pacer.say("dev", long)
    for _ in range(int(MAX_WAIT_S / duration(long)) + 2):
        await pacer.say("dev", long)
    caplog.set_level(logging.INFO, "alice_bridge.pacing")
    await settle(pacer)
    assert "dropped a queued phrase" in caplog.text
    queued_ats = sorted(at for at, _, t in speaker.said if t == long)
    assert queued_ats[-1] - 1000.0 <= MAX_WAIT_S + duration(long) + 5


async def test_failed_signal_still_speaks_the_answer(clock):
    speaker = Speaker(clock, results=[Speech.OK, Speech.ERROR, Speech.OK])
    pacer = Pacer(speaker, clock=clock, sleep=clock.sleep)
    await pacer.say("dev", "Раз.")
    await pacer.say("dev", "Два.")
    await settle(pacer)
    assert [t for _, _, t in speaker.said] == ["Раз.", SIGNAL, "Два."]


async def test_queued_text_not_logged_at_info(clock, caplog):
    speaker = Speaker(clock)
    pacer = Pacer(speaker, clock=clock, sleep=clock.sleep)
    caplog.set_level(logging.INFO, "alice_bridge.pacing")
    await pacer.say("dev", "Раз.")
    await pacer.say("dev", "секретный текст")
    await settle(pacer)
    assert "секретный" not in caplog.text
    assert "spoke queued on dev: ok" in caplog.text
