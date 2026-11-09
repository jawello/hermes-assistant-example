"""Tests for the CDP-over-HTTP/websocket helpers used to sweep dead tabs.

The socket layer is exercised through a fake socket; the sweep policy is
exercised with the liveness probe injected, because what matters is that a
responsive tab is NEVER closed."""
import json
import struct
from unittest.mock import MagicMock, patch

import pytest

from login_broker import cdp


class FakeSocket:
    """Records what the client sent; replays canned server frames."""

    def __init__(self, frames=()):
        # No handshake bytes: these tests drive the frame layer directly, with
        # _ws_connect patched out, so the stream starts at the first frame.
        self.sent = b""
        self._inbox = b"".join(frames)
        self.closed = False

    def sendall(self, data):
        self.sent += data

    def recv(self, n):
        chunk, self._inbox = self._inbox[:n], self._inbox[n:]
        return chunk

    def settimeout(self, _):
        pass

    def close(self):
        self.closed = True


def server_frame(text, opcode=0x1, fin=True):
    """A server->client frame: never masked, per RFC 6455."""
    payload = text.encode()
    b0 = (0x80 if fin else 0x00) | opcode
    if len(payload) < 126:
        return struct.pack("!BB", b0, len(payload)) + payload
    return struct.pack("!BBH", b0, 126, len(payload)) + payload


def test_client_frames_are_masked_as_the_rfc_requires():
    # Chrome drops an unmasked client frame and the probe would hang, which the
    # sweep would then misread as "this tab is dead" and close a live tab.
    sock = FakeSocket()
    cdp._send_text(sock, "hello")
    assert sock.sent[0] == 0x81           # FIN + text opcode
    assert sock.sent[1] & 0x80            # MASK bit set
    assert sock.sent[1] & 0x7F == 5       # payload length
    mask = sock.sent[2:6]
    unmasked = bytes(b ^ mask[i % 4] for i, b in enumerate(sock.sent[6:]))
    assert unmasked == b"hello"


def test_receive_reassembles_a_fragmented_text_message():
    sock = FakeSocket(frames=[server_frame('{"id":', fin=False),
                              server_frame('1}', opcode=0x0, fin=True)])
    assert cdp._recv_text(sock) == '{"id":1}'


def test_receive_skips_ping_and_pong_control_frames():
    sock = FakeSocket(frames=[server_frame("", opcode=0x9),
                              server_frame('{"id":1}')])
    assert cdp._recv_text(sock) == '{"id":1}'


def test_receive_raises_on_a_close_frame_instead_of_hanging():
    sock = FakeSocket(frames=[server_frame("", opcode=0x8)])
    with pytest.raises(ConnectionError):
        cdp._recv_text(sock)


def test_sweep_closes_only_the_unresponsive_targets():
    targets = [
        {"id": "live", "type": "page", "url": "https://a", "webSocketDebuggerUrl": "ws://x/live"},
        {"id": "dead", "type": "page", "url": "https://b", "webSocketDebuggerUrl": "ws://x/dead"},
    ]
    closed = []
    with patch.object(cdp, "list_targets", return_value=targets), \
         patch.object(cdp, "close_target", side_effect=lambda base, tid: closed.append(tid)), \
         patch.object(cdp, "is_responsive", side_effect=lambda ws, timeout: ws.endswith("live")):
        assert cdp.sweep_unresponsive_targets("http://cdp:9222") == 1
    assert closed == ["dead"]


def test_sweep_leaves_targets_without_a_websocket_url_alone():
    # No webSocketDebuggerUrl means something is already attached to it (a live
    # Playwright session, possibly a concurrent login). Not ours to judge.
    targets = [{"id": "attached", "type": "page", "url": "https://a"}]
    with patch.object(cdp, "list_targets", return_value=targets), \
         patch.object(cdp, "close_target") as close, \
         patch.object(cdp, "is_responsive", return_value=False):
        assert cdp.sweep_unresponsive_targets("http://cdp:9222") == 0
    close.assert_not_called()


def test_sweep_ignores_non_page_targets():
    targets = [{"id": "sw", "type": "service_worker", "url": "https://a",
                "webSocketDebuggerUrl": "ws://x/sw"}]
    with patch.object(cdp, "list_targets", return_value=targets), \
         patch.object(cdp, "close_target") as close, \
         patch.object(cdp, "is_responsive", return_value=False):
        assert cdp.sweep_unresponsive_targets("http://cdp:9222") == 0
    close.assert_not_called()


def test_sweep_survives_an_unreachable_cdp_endpoint():
    # The sweep is a courtesy, never a gate: if it cannot even list targets the
    # login must still be attempted.
    with patch.object(cdp, "list_targets", side_effect=OSError("connection refused")):
        assert cdp.sweep_unresponsive_targets("http://cdp:9222") == 0


def test_sweep_keeps_going_when_one_close_fails():
    targets = [
        {"id": "d1", "type": "page", "url": "https://a", "webSocketDebuggerUrl": "ws://x/1"},
        {"id": "d2", "type": "page", "url": "https://b", "webSocketDebuggerUrl": "ws://x/2"},
    ]
    def flaky(base, tid):
        if tid == "d1":
            raise OSError("gone")
    with patch.object(cdp, "list_targets", return_value=targets), \
         patch.object(cdp, "close_target", side_effect=flaky), \
         patch.object(cdp, "is_responsive", return_value=False):
        assert cdp.sweep_unresponsive_targets("http://cdp:9222") == 1


def test_is_responsive_is_false_when_the_renderer_never_answers():
    import socket as socket_mod
    sock = FakeSocket()
    sock.recv = MagicMock(side_effect=socket_mod.timeout("timed out"))
    with patch.object(cdp, "_ws_connect", return_value=sock):
        assert cdp.is_responsive("ws://x/1", timeout=1) is False


def test_is_responsive_is_true_on_a_matching_reply():
    reply = json.dumps({"id": 1, "result": {"result": {"value": 2}}})
    sock = FakeSocket(frames=[server_frame(reply)])
    with patch.object(cdp, "_ws_connect", return_value=sock):
        assert cdp.is_responsive("ws://x/1", timeout=1) is True
    assert sock.closed


def test_is_responsive_ignores_unsolicited_events_before_the_reply():
    frames = [server_frame(json.dumps({"method": "Page.frameNavigated"})),
              server_frame(json.dumps({"id": 1, "result": {}}))]
    sock = FakeSocket(frames=frames)
    with patch.object(cdp, "_ws_connect", return_value=sock):
        assert cdp.is_responsive("ws://x/1", timeout=1) is True


def _targets(*ids):
    return [{"id": i, "type": "page", "url": f"https://{i}", "webSocketDebuggerUrl": f"ws://x/{i}"}
            for i in ids]


def _sweep_with_suspects(targets, dead, suspects):
    """One periodic sweep pass: `dead` are the ids failing the probe now."""
    closed = []
    with patch.object(cdp, "list_targets", return_value=targets), \
         patch.object(cdp, "close_target", side_effect=lambda base, tid: closed.append(tid)), \
         patch.object(cdp, "is_responsive", side_effect=lambda ws, timeout: ws.rsplit("/", 1)[1] not in dead):
        count = cdp.sweep_unresponsive_targets("http://cdp:9222", suspects=suspects)
    assert count == len(closed)
    return closed


def test_periodic_sweep_waits_for_a_second_failed_probe():
    # A heavy page can go quiet past the probe timeout while hermes is using
    # it; closing on the first miss would kill a live tab mid-task.
    suspects = set()
    assert _sweep_with_suspects(_targets("a", "b"), dead={"b"}, suspects=suspects) == []
    assert suspects == {"b"}
    assert _sweep_with_suspects(_targets("a", "b"), dead={"b"}, suspects=suspects) == ["b"]
    assert suspects == set()


def test_periodic_sweep_forgives_a_tab_that_came_back():
    suspects = set()
    _sweep_with_suspects(_targets("a"), dead={"a"}, suspects=suspects)
    assert _sweep_with_suspects(_targets("a"), dead=set(), suspects=suspects) == []
    assert suspects == set()
    # A later miss starts the count again rather than closing at once.
    assert _sweep_with_suspects(_targets("a"), dead={"a"}, suspects=suspects) == []


def test_periodic_sweep_never_closes_a_live_tab():
    suspects = {"a"}  # even one remembered as a suspect
    assert _sweep_with_suspects(_targets("a"), dead=set(), suspects=suspects) == []


def test_periodic_sweep_forgets_tabs_that_are_gone():
    suspects = {"gone"}
    _sweep_with_suspects(_targets("a"), dead=set(), suspects=suspects)
    assert suspects == set()


def test_periodic_sweep_keeps_suspects_when_listing_fails():
    suspects = {"a"}
    with patch.object(cdp, "list_targets", side_effect=OSError("refused")):
        assert cdp.sweep_unresponsive_targets("http://cdp:9222", suspects=suspects) == 0
    assert suspects == {"a"}


def test_sweep_forever_sweeps_until_stopped_and_survives_errors():
    import threading
    stop = threading.Event()
    calls = []

    def fake_sweep(url, suspects):
        calls.append(url)
        if len(calls) == 1:
            raise RuntimeError("boom")  # must not end the loop
        if len(calls) == 3:
            stop.set()
        return 0

    with patch.object(cdp, "sweep_unresponsive_targets", side_effect=fake_sweep):
        cdp.sweep_forever("http://cdp:9222", interval_s=0, stop=stop)
    assert calls == ["http://cdp:9222"] * 3


def test_periodic_sweep_retries_a_failed_close_on_the_next_pass():
    suspects = {"a"}
    with patch.object(cdp, "list_targets", return_value=_targets("a")), \
         patch.object(cdp, "close_target", side_effect=OSError("gone")), \
         patch.object(cdp, "is_responsive", return_value=False):
        assert cdp.sweep_unresponsive_targets("http://cdp:9222", suspects=suspects) == 0
    assert suspects == {"a"}


# --- Old tabs: closed when the browser burns CPU, or after a long timeout -----
#
# 2026-10-12: a tab left by hermes kept chromium at ~190% CPU on the 2-vCPU
# host for 44 h while answering every liveness probe. CDP reports CPU only for
# the whole browser (video is drawn by one shared GPU process), so a tab is
# closed for CPU only when BOTH hold: the browser has been hot for two passes in
# a row AND the tab is at least 30 min old. Young tabs are what hermes is
# working with right now. Any tab is closed after 6 h, hot or not.

LIMITS = cdp.TabLimits(hot_age_s=1800, max_age_s=21600, cpu_limit=0.5)
HOT, COOL = 1.9, 0.02  # cores used between passes


class Browser:
    """A fake browser: open page targets and a cumulative CPU counter."""

    def __init__(self, *ids):
        self.ids = list(ids)
        self.cpu = 0.0
        self.closed = []
        self.browser_closed = 0
        self.cpu_fails = False

    def targets(self, _base):
        return [{"id": i, "type": "page", "url": f"https://{i}",
                 "webSocketDebuggerUrl": f"ws://x/{i}"} for i in self.ids]

    def close(self, _base, tid):
        self.closed.append(tid)
        self.ids.remove(tid)

    def cpu_seconds(self, _base):
        if self.cpu_fails:
            raise OSError("refused")
        return self.cpu

    def close_browser(self, _base):
        self.browser_closed += 1
        self.ids.clear()


def run_pass(browser, state, now, limits=LIMITS):
    with patch.object(cdp, "list_targets", side_effect=browser.targets), \
         patch.object(cdp, "close_target", side_effect=browser.close), \
         patch.object(cdp, "browser_cpu_seconds", side_effect=browser.cpu_seconds), \
         patch.object(cdp, "close_browser", side_effect=browser.close_browser):
        cdp.close_old_targets("http://cdp:9222", state, limits, now=now)


def run_passes(browser, state, start, end, cores, step=180):
    """Passes every `step` s in [start, end]; before each one the browser has
    burnt `cores` since the previous pass."""
    t = start
    while t <= end:
        browser.cpu += cores * step
        run_pass(browser, state, t)
        t += step


def test_old_tab_is_closed_when_the_browser_is_hot():
    browser, state = Browser("a"), cdp.SweepState()
    run_passes(browser, state, 0, 1620, COOL)
    assert browser.closed == []
    browser.cpu += HOT * 180
    run_pass(browser, state, 1800)          # hot once: not yet
    assert browser.closed == []
    browser.cpu += HOT * 180
    run_pass(browser, state, 1980)          # hot twice and 33 min old
    assert browser.closed == ["a"]


def test_old_tab_stays_while_the_browser_is_cool():
    browser, state = Browser("a"), cdp.SweepState()
    run_passes(browser, state, 0, 21600 - 180, COOL)
    assert browser.closed == []


def test_young_tab_stays_even_when_the_browser_is_hot():
    browser, state = Browser("old"), cdp.SweepState()
    run_passes(browser, state, 0, 1260, COOL)
    browser.ids.append("young")             # hermes opens a tab at 24 min
    run_passes(browser, state, 1440, 2520, HOT)
    assert browser.closed == ["old"]
    assert browser.ids == ["young"]
    run_passes(browser, state, 2700, 3060, HOT)
    assert browser.closed == ["old"]
    run_passes(browser, state, 3240, 3240, HOT)
    assert browser.closed == ["old", "young"]  # 30 min after it was first seen


def test_any_tab_is_closed_after_six_hours():
    browser, state = Browser("a"), cdp.SweepState()
    run_passes(browser, state, 0, 21600 - 180, COOL)
    assert browser.closed == []
    browser.cpu += COOL * 180
    run_pass(browser, state, 21600)
    assert browser.closed == ["a"]


def test_one_short_spike_closes_nothing():
    browser, state = Browser("a"), cdp.SweepState()
    run_passes(browser, state, 0, 1800, COOL)
    browser.cpu += HOT * 180
    run_pass(browser, state, 1980)
    browser.cpu += COOL * 180
    run_pass(browser, state, 2160)
    browser.cpu += HOT * 180
    run_pass(browser, state, 2340)
    assert browser.closed == []


def test_unreadable_cpu_closes_nothing_for_cpu_but_six_hours_still_apply():
    browser, state = Browser("a"), cdp.SweepState()
    browser.cpu_fails = True
    run_passes(browser, state, 0, 21600 - 180, HOT)
    assert browser.closed == []
    run_pass(browser, state, 21600)
    assert browser.closed == ["a"]


def test_browser_is_closed_when_it_stays_hot_with_no_tabs_for_30_minutes():
    browser, state = Browser(), cdp.SweepState()
    run_passes(browser, state, 0, 1620, HOT)
    assert browser.browser_closed == 0
    run_passes(browser, state, 1800, 1800, HOT)  # hot since the pass at 0
    assert browser.browser_closed == 1


def test_browser_is_not_closed_while_it_has_tabs():
    # Hot with young tabs means hermes is busy in it; the tabs' own age rule
    # covers them.
    browser, state = Browser(), cdp.SweepState()
    t = 0
    while t <= 3600:
        browser.ids = [f"t{t}"]             # a fresh tab every pass
        if t:
            browser.cpu += HOT * 180
        run_pass(browser, state, t)
        t += 180
    assert browser.browser_closed == 0


def test_ages_of_closed_tabs_are_forgotten():
    browser, state = Browser("a"), cdp.SweepState()
    run_pass(browser, state, 0)
    browser.ids.clear()
    run_pass(browser, state, 180)
    assert state.first_seen == {}


def test_zero_limits_turn_the_rules_off():
    off = cdp.TabLimits(hot_age_s=0, max_age_s=0, cpu_limit=0)
    browser, state = Browser("a"), cdp.SweepState()
    t = 0
    while t <= 30000:
        browser.cpu += HOT * 180
        run_pass(browser, state, t, limits=off)
        t += 180
    assert browser.closed == [] and browser.browser_closed == 0


def test_listing_failure_closes_nothing_and_keeps_ages():
    browser, state = Browser("a"), cdp.SweepState()
    run_pass(browser, state, 0)
    with patch.object(cdp, "list_targets", side_effect=OSError("refused")), \
         patch.object(cdp, "close_target") as close:
        cdp.close_old_targets("http://cdp:9222", state, LIMITS, now=30000)
    close.assert_not_called()
    assert state.first_seen == {"a": 0}


def test_sweep_forever_also_closes_old_tabs_when_limits_are_given():
    import threading
    stop = threading.Event()
    seen = []

    def fake_close_old(url, state, limits):
        seen.append((url, limits))
        stop.set()

    with patch.object(cdp, "sweep_unresponsive_targets", return_value=0), \
         patch.object(cdp, "close_old_targets", side_effect=fake_close_old):
        cdp.sweep_forever("http://cdp:9222", interval_s=0, stop=stop, limits=LIMITS)
    assert seen == [("http://cdp:9222", LIMITS)]


def test_browser_cpu_is_the_sum_over_all_its_processes():
    # The GPU process is where swiftshader draws video; leaving it out would
    # miss the very load this rule exists for.
    reply = json.dumps({"id": 1, "result": {"processInfo": [
        {"type": "browser", "id": 1, "cpuTime": 10.5},
        {"type": "GPU", "id": 22, "cpuTime": 300.25},
        {"type": "renderer", "id": 40, "cpuTime": 2.0}]}})
    sock = FakeSocket(frames=[server_frame(reply)])
    with patch.object(cdp, "_browser_ws_url", return_value="ws://x/browser"), \
         patch.object(cdp, "_ws_connect", return_value=sock):
        assert cdp.browser_cpu_seconds("http://cdp:9222") == 312.75
    assert sock.closed


def test_browser_cpu_raises_on_a_cdp_error_so_it_counts_as_no_reading():
    reply = json.dumps({"id": 1, "error": {"code": -32601, "message": "not found"}})
    sock = FakeSocket(frames=[server_frame(reply)])
    with patch.object(cdp, "_browser_ws_url", return_value="ws://x/browser"), \
         patch.object(cdp, "_ws_connect", return_value=sock), \
         pytest.raises(RuntimeError):
        cdp.browser_cpu_seconds("http://cdp:9222")


def test_closing_the_browser_tolerates_it_vanishing_mid_reply():
    sock = FakeSocket(frames=[server_frame("", opcode=0x8)])  # closed by peer
    with patch.object(cdp, "_browser_ws_url", return_value="ws://x/browser"), \
         patch.object(cdp, "_ws_connect", return_value=sock):
        cdp.close_browser("http://cdp:9222")
    mask = sock.sent[2:6]
    sent = bytes(b ^ mask[i % 4] for i, b in enumerate(sock.sent[6:]))
    assert json.loads(sent)["method"] == "Browser.close"
