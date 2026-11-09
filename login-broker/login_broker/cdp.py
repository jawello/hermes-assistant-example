"""Minimal CDP client used to clear dead tabs out of the shared browser before
Playwright attaches to it.

Why this exists at all: `connect_over_cdp` auto-attaches to every target in the
browser, so one abandoned tab whose renderer has stopped answering stalls the
attach until it times out — and every browser_login then fails with a reason
that reads exactly like rejected credentials. Production reached 8 abandoned
tabs, 5 of them wedged (two YouTube players, two Google Maps, one Notion),
which is how a working mobile-number login came to look like a broken one.

Playwright cannot do this job: it is the thing that hangs. The HTTP endpoints
(`/json/list`, `/json/close`) are served by the browser process and keep
answering while renderers are wedged, and the liveness probe needs a raw
websocket. Chrome's CDP port carries no auth and the container ships no
websocket library, so a small stdlib client is cheaper than a dependency in the
one container that handles secrets. Only `page` targets are considered, only
targets that fail the probe are closed, and every failure is swallowed — the
sweep is a courtesy before the real work, never a gate in front of it.
"""
import base64
import json
import os
import socket
import struct
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import urlparse

# A page mid-navigation can legitimately go quiet for a moment, and closing a
# live tab is far worse than leaving a dead one — so the probe waits longer than
# it strictly needs to. Targets are probed in parallel, so this is roughly the
# whole sweep's cost, not its cost per tab.
PROBE_TIMEOUT_S = 5
MAX_PARALLEL_PROBES = 8
_TEXT, _BINARY, _CLOSE, _PING, _PONG, _CONTINUATION = 0x1, 0x2, 0x8, 0x9, 0xA, 0x0


def _ws_connect(ws_url: str, timeout: float):
    """Open a websocket to a CDP target. Speaks just enough of RFC 6455 to
    carry one request and one reply."""
    u = urlparse(ws_url)
    sock = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
    sock.settimeout(timeout)
    path = u.path + (f"?{u.query}" if u.query else "")
    key = base64.b64encode(os.urandom(16)).decode()
    sock.sendall(
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {u.hostname}:{u.port}\r\n"
        f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode()
    )
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            sock.close()
            raise ConnectionError("websocket handshake closed early")
        buf += chunk
    status = buf.split(b"\r\n", 1)[0]
    if b" 101" not in status:
        sock.close()
        raise ConnectionError(f"websocket handshake refused: {status!r}")
    return sock


def _send_text(sock, text: str) -> None:
    """Send one text frame. Client frames MUST be masked or Chrome drops them
    silently, which would make every tab look dead."""
    payload = text.encode()
    n = len(payload)
    if n < 126:
        header = struct.pack("!BB", 0x80 | _TEXT, 0x80 | n)
    elif n < 65536:
        header = struct.pack("!BBH", 0x80 | _TEXT, 0x80 | 126, n)
    else:
        header = struct.pack("!BBQ", 0x80 | _TEXT, 0x80 | 127, n)
    mask = os.urandom(4)
    sock.sendall(header + mask
                 + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))


def _recv_exactly(sock, n: int) -> bytes:
    out = b""
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            raise ConnectionError("websocket closed mid-frame")
        out += chunk
    return out


def _recv_text(sock) -> str:
    """Read one complete text message, reassembling fragments and skipping
    control frames. A close frame raises rather than looping forever."""
    parts = []
    while True:
        b0, b1 = _recv_exactly(sock, 2)
        fin, opcode = b0 & 0x80, b0 & 0x0F
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack("!H", _recv_exactly(sock, 2))[0]
        elif n == 127:
            n = struct.unpack("!Q", _recv_exactly(sock, 8))[0]
        data = _recv_exactly(sock, n) if n else b""
        if b1 & 0x80:  # a masked server frame is illegal, but unmask defensively
            mask, data = data[:4], data[4:]
            data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
        if opcode == _CLOSE:
            raise ConnectionError("websocket closed by peer")
        if opcode in (_PING, _PONG):
            continue
        parts.append(data)
        if fin:
            return b"".join(parts).decode(errors="replace")


def list_targets(cdp_http_url: str) -> list[dict]:
    with urllib.request.urlopen(f"{cdp_http_url}/json/list", timeout=15) as resp:
        return json.load(resp)


def close_target(cdp_http_url: str, target_id: str) -> None:
    with urllib.request.urlopen(
            f"{cdp_http_url}/json/close/{target_id}", timeout=10) as resp:
        resp.read()


def is_responsive(ws_url: str, timeout: float = PROBE_TIMEOUT_S) -> bool:
    """True iff the target's renderer answers a trivial Runtime.evaluate. This
    is what separates a merely idle tab from a wedged one: the browser process
    keeps listing wedged tabs and accepting websockets for them, but nothing
    behind them ever replies."""
    try:
        sock = _ws_connect(ws_url, timeout)
    except Exception:
        return False
    try:
        _send_text(sock, json.dumps({
            "id": 1, "method": "Runtime.evaluate",
            "params": {"expression": "1+1", "returnByValue": True},
        }))
        for _ in range(10):  # a target may push events before our reply
            if json.loads(_recv_text(sock)).get("id") == 1:
                return True
        return False
    except Exception:
        return False
    finally:
        try:
            sock.close()
        except Exception:
            pass


def sweep_unresponsive_targets(cdp_http_url: str,
                              timeout: float = PROBE_TIMEOUT_S,
                              suspects: set | None = None) -> int:
    """Close every `page` target whose renderer fails the liveness probe and
    return how many were closed. Best-effort throughout: a target with no
    webSocketDebuggerUrl is already attached by somebody (possibly a concurrent
    login) and is left alone, and any error — including an unreachable CDP
    endpoint — yields 0 rather than blocking the login that follows.

    With `suspects` (the periodic sweep), a target is closed only when it
    failed the previous pass too: a heavy page can go quiet past the probe
    timeout while hermes is driving it, and a wedged one never comes back.
    The set is updated in place to this pass's survivors that failed."""
    try:
        targets = list_targets(cdp_http_url)
    except Exception as exc:
        print(f"cdp sweep: could not list targets ({type(exc).__name__})",
              file=sys.stderr, flush=True)
        return 0

    candidates = [t for t in targets
                  if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
    if not candidates:
        if suspects is not None:
            suspects.clear()
        return 0
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_PROBES) as pool:
        alive = list(pool.map(
            lambda t: is_responsive(t["webSocketDebuggerUrl"], timeout=timeout),
            candidates))

    closed = 0
    failed_now = set()
    for target, responsive in zip(candidates, alive):
        if responsive:
            continue
        if suspects is not None and target["id"] not in suspects:
            failed_now.add(target["id"])
            continue
        try:
            close_target(cdp_http_url, target["id"])
        except Exception as exc:
            print(f"cdp sweep: close failed ({type(exc).__name__})",
                  file=sys.stderr, flush=True)
            failed_now.add(target["id"])  # still dead: retry on the next pass
            continue
        closed += 1
        # The URL is a page the browser was already showing, not a credential.
        print(f"cdp sweep: closed unresponsive tab {target.get('url', '')[:80]}",
              file=sys.stderr, flush=True)
    if suspects is not None:
        suspects.clear()
        suspects.update(failed_now)
    return closed


@dataclass(frozen=True)
class TabLimits:
    """When the periodic sweep closes tabs that still answer the probe.

    hot_age_s: a tab this old is closed while the browser is hot.
    max_age_s: a tab this old is closed whatever the CPU.
    cpu_limit: cores the whole browser may burn between passes before it
    counts as hot. Zero turns a rule off."""
    hot_age_s: float = 1800
    max_age_s: float = 21600
    cpu_limit: float = 0.5


# A page load or a screenshot is a burst; a runaway page burns for good. Two
# hot passes in a row (~6 min at the default interval) tell them apart.
HOT_PASSES = 2


@dataclass
class SweepState:
    """What the periodic sweep remembers between passes."""
    suspects: set = field(default_factory=set)
    # CDP does not say when a tab was opened, so its age counts from the
    # first pass that saw it.
    first_seen: dict = field(default_factory=dict)
    cpu_sample: tuple | None = None  # (browser cpu seconds, at)
    hot_passes: int = 0
    hot_since: float | None = None


def _cdp_call(ws_url: str, method: str, timeout: float = PROBE_TIMEOUT_S) -> dict:
    """One CDP command over a fresh websocket; returns its `result`."""
    sock = _ws_connect(ws_url, timeout)
    try:
        _send_text(sock, json.dumps({"id": 1, "method": method}))
        for _ in range(10):  # a target may push events before our reply
            reply = json.loads(_recv_text(sock))
            if reply.get("id") == 1:
                if "error" in reply:
                    raise RuntimeError(f"{method}: {reply['error']}")
                return reply.get("result", {})
        raise TimeoutError(f"{method}: no reply")
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _browser_ws_url(cdp_http_url: str) -> str:
    with urllib.request.urlopen(f"{cdp_http_url}/json/version", timeout=10) as resp:
        return json.load(resp)["webSocketDebuggerUrl"]


def browser_cpu_seconds(cdp_http_url: str) -> float:
    """CPU time all browser processes have used, in seconds. Per tab is not
    available: video and animation are drawn (in software, swiftshader) by one
    GPU process shared by every tab."""
    info = _cdp_call(_browser_ws_url(cdp_http_url), "SystemInfo.getProcessInfo")
    return sum(p.get("cpuTime", 0.0) for p in info.get("processInfo", []))


def close_browser(cdp_http_url: str) -> None:
    """Close the whole browser. Chrome is the container's main process, so the
    container exits and Docker starts it again; the profile is on a volume."""
    try:
        _cdp_call(_browser_ws_url(cdp_http_url), "Browser.close")
    except (ConnectionError, OSError):
        pass  # the browser going away mid-reply is the expected outcome


def _read_cpu(cdp_http_url: str, state: SweepState, limits: TabLimits,
              now: float) -> float | None:
    """Update the hot streak; return cores used since the last pass, or None
    when there is no reading (rule off, counter unreadable, first pass, or a
    restarted browser whose counter went back)."""
    def no_reading():
        state.hot_passes, state.hot_since = 0, None
        return None

    if not limits.cpu_limit:
        state.cpu_sample = None
        return no_reading()
    try:
        cpu = browser_cpu_seconds(cdp_http_url)
    except Exception as exc:
        print(f"cdp sweep: could not read browser cpu ({type(exc).__name__})",
              file=sys.stderr, flush=True)
        state.cpu_sample = None
        return no_reading()
    prev, state.cpu_sample = state.cpu_sample, (cpu, now)
    if prev is None or now <= prev[1] or cpu < prev[0]:
        return no_reading()
    cores = (cpu - prev[0]) / (now - prev[1])
    if cores > limits.cpu_limit:
        if state.hot_passes == 0:
            state.hot_since = prev[1]
        state.hot_passes += 1
    else:
        state.hot_passes, state.hot_since = 0, None
    return cores


def close_old_targets(cdp_http_url: str, state: SweepState, limits: TabLimits,
                      now: float | None = None) -> int:
    """Close tabs that answer the probe but should not be open any more, and
    return how many were closed.

    2026-10-12: a tab left behind by hermes kept chromium at ~190% CPU on the
    2-vCPU host for 44 h while answering every probe. A tab is closed for CPU
    only when the browser is hot AND the tab is at least `hot_age_s` old:
    young tabs are what hermes is working with right now. Any tab is closed
    after `max_age_s`. When the browser stays hot for `hot_age_s` with no tab
    open, the browser itself is closed. Best-effort like the probe sweep."""
    now = time.monotonic() if now is None else now
    try:
        targets = list_targets(cdp_http_url)
    except Exception as exc:
        print(f"cdp sweep: could not list targets ({type(exc).__name__})",
              file=sys.stderr, flush=True)
        return 0

    pages = [t for t in targets if t.get("type") == "page"]
    ids = {t["id"] for t in pages}
    for gone in set(state.first_seen) - ids:
        del state.first_seen[gone]
    for t in pages:
        state.first_seen.setdefault(t["id"], now)

    cores = _read_cpu(cdp_http_url, state, limits, now)
    hot = state.hot_passes >= HOT_PASSES

    closed = 0
    for t in pages:
        age = now - state.first_seen[t["id"]]
        if limits.max_age_s and age >= limits.max_age_s:
            reason = f"open {age / 3600:.1f} h"
        elif hot and limits.hot_age_s and age >= limits.hot_age_s:
            reason = f"open {age / 60:.0f} min while the browser burns {cores:.2f} cores"
        else:
            continue
        try:
            close_target(cdp_http_url, t["id"])
        except Exception as exc:
            print(f"cdp sweep: close failed ({type(exc).__name__})",
                  file=sys.stderr, flush=True)
            continue
        del state.first_seen[t["id"]]
        closed += 1
        print(f"cdp sweep: closed tab {t.get('url', '')[:80]}: {reason}",
              file=sys.stderr, flush=True)

    if (hot and limits.hot_age_s and not pages
            and now - state.hot_since >= limits.hot_age_s):
        print(f"cdp sweep: closing the browser: no tabs, {cores:.2f} cores for "
              f"{(now - state.hot_since) / 60:.0f} min", file=sys.stderr, flush=True)
        try:
            close_browser(cdp_http_url)
        except Exception as exc:
            print(f"cdp sweep: browser close failed ({type(exc).__name__})",
                  file=sys.stderr, flush=True)
        state.cpu_sample, state.hot_passes, state.hot_since = None, 0, None
    return closed


def sweep_forever(cdp_http_url: str, interval_s: float, stop,
                  limits: TabLimits | None = None) -> None:
    """Sweep the shared browser every `interval_s` until `stop` is set.

    The sweep before browser_login only helps logins: a tab that wedges while
    hermes drives the browser itself (browser_navigate) stalled every later
    attach for four days (2026-10-07..11). With `limits`, each pass also
    closes old tabs (close_old_targets). Each pass is a courtesy like the
    login one; an error is logged and the next pass still runs."""
    state = SweepState()
    while not stop.is_set():
        try:
            sweep_unresponsive_targets(cdp_http_url, suspects=state.suspects)
        except Exception as exc:
            print(f"cdp sweep: periodic pass failed ({type(exc).__name__})",
                  file=sys.stderr, flush=True)
        if limits is not None:
            try:
                close_old_targets(cdp_http_url, state, limits)
            except Exception as exc:
                print(f"cdp sweep: old-tab pass failed ({type(exc).__name__})",
                      file=sys.stderr, flush=True)
        stop.wait(interval_s)
