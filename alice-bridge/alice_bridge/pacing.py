"""Keep answers on a Station from cutting each other off.

Verified by ear on 2026-11-09: a Quasar scenario run sent while the Station
is still speaking cuts the phrase that plays (plain text and sounds alike,
0.2 s and 4 s into it). Yandex keeps no queue, so this does.

A phrase for a free Station is spoken at once and its result goes back to
hermes, as before. A phrase that arrives while the Station is (by estimate)
still speaking, or soon after it stopped, is queued: hermes hears "queued"
at once, so the Telegram answer is not held up, and the phrase is spoken
after the previous one ends, preceded by a short signal so the user can tell
two answers apart (2 s of silence was not enough). A queued phrase's
failure only reaches the log.

The Station's state is not readable through the cloud API, so speaking time
is estimated from the text length, on the generous side.
"""
import asyncio
import collections
import logging
import time
from dataclasses import dataclass, field

from alice_bridge.quasar import SAY_DEADLINE_S, Speech

log = logging.getLogger("alice_bridge.pacing")

# Alice's sound library; heard on the Station Mini on 2026-11-09. The tag
# counts toward Yandex's 100 characters, so it goes as a phrase of its own.
SIGNAL = '<speaker audio="alice-sounds-game-ping-1.opus">'
START_S = 1.5          # from the run request to the first sound
CHARS_PER_S = 12.0     # Alice reads Russian at about 13-15 characters a second
SIGNAL_S = 2.5         # the signal, start included
GAP_S = 0.5            # silence after the previous answer, before the signal
NEAR_S = 10.0          # an answer this soon after the last one gets the signal
MAX_WAIT_S = 60.0      # a phrase queued longer than this is no longer timely


def duration(text: str) -> float:
    """How long the Station is busy after a run of this text, estimated."""
    if text == SIGNAL:
        return SIGNAL_S
    return START_S + len(text) / CHARS_PER_S


@dataclass
class _Station:
    busy_until: float = float("-inf")
    queue: collections.deque = field(default_factory=collections.deque)
    worker: asyncio.Task | None = None


class Pacer:
    """Wraps QuasarSpeaker (start/say) with per-Station pacing."""

    def __init__(self, speaker, *, clock=time.monotonic, sleep=asyncio.sleep):
        self._speaker = speaker
        self._clock = clock
        self._sleep = sleep
        self._stations: dict[str, _Station] = {}

    async def start(self) -> Speech:
        return await self._speaker.start()

    def pending(self) -> list[asyncio.Task]:
        return [s.worker for s in self._stations.values() if s.worker is not None]

    async def say(self, device_id: str, text: str) -> Speech:
        station = self._stations.setdefault(device_id, _Station())
        now = self._clock()
        if station.worker is None and now >= station.busy_until + NEAR_S:
            before = station.busy_until
            # Claim the Station before the request, so a phrase arriving
            # meanwhile is queued instead of cutting this one.
            station.busy_until = now + SAY_DEADLINE_S + duration(text)
            result = await self._speaker.say(device_id, text)
            station.busy_until = self._clock() + duration(text) if result is Speech.OK else before
            return result
        station.queue.append((now, text))
        if station.worker is None:
            station.worker = asyncio.create_task(self._drain(device_id, station))
        return Speech.QUEUED

    async def _drain(self, device_id: str, station: _Station) -> None:
        try:
            while station.queue:
                queued_at, text = station.queue.popleft()
                await self._sleep(station.busy_until + GAP_S - self._clock())
                if self._clock() - queued_at > MAX_WAIT_S:
                    log.warning("dropped a queued phrase for %s after %.0fs", device_id,
                                self._clock() - queued_at)
                    continue
                if await self._speaker.say(device_id, SIGNAL) is Speech.OK:
                    await self._sleep(duration(SIGNAL))
                result = await self._speaker.say(device_id, text)
                log.info("spoke queued on %s: %s after %.1fs", device_id, result,
                         self._clock() - queued_at)
                if result is Speech.OK:
                    station.busy_until = self._clock() + duration(text)
        except Exception:  # speech must never take the bridge down
            log.exception("speech queue for %s failed", device_id)
            station.queue.clear()
        finally:
            station.worker = None

    async def close(self) -> None:
        for task in self.pending():
            task.cancel()
