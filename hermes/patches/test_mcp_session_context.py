"""Tests for the hermes-agent MCP session-context patch.

Two halves: that the patch lands in the right place in a file shaped like
upstream's, and that the function it injects actually carries a session
across a thread boundary the way the MCP loop crosses one.
"""
import ast
import asyncio
import sys
import threading
from contextvars import ContextVar

from pathlib import Path

import pytest

import mcp_session_context as patch

# A file shaped like tools/mcp_tool_loop.py around the scheduling helper
# (hermes-agent 0.21.5). Only the lines the patch reasons about are real; the
# rest is there so a failure is about placement rather than about a one-line
# fixture.
UPSTREAM = '''\
def _wrap_with_home_override(coro):
    return coro


def _wrap_with_dashboard_oauth_flow(coro):
    return coro


def _run_on_mcp_loop(coro_or_factory, timeout: float = 30):
    """Schedule a coroutine on the MCP loop and block until done."""
    loop = _running_loop()
    # run_coroutine_threadsafe copies the LOOP thread's context, so a per-request profile scope
    # would vanish here; re-establish it inside the task's own context.
    coro = _wrap_with_dashboard_oauth_flow(_wrap_with_home_override(
        coro_or_factory() if callable(coro_or_factory) else coro_or_factory))
    future = safe_schedule_threadsafe(coro, loop)
    return future.result(timeout)
'''


def test_the_patch_calls_the_wrapper_right_before_scheduling():
    out = patch.patched_source(UPSTREAM)
    lines = [line.strip() for line in out.splitlines()]
    schedule = lines.index("future = safe_schedule_threadsafe(coro, loop)")
    assert lines[schedule - 1] == "coro = _wrap_with_session_context(coro)"


def test_the_patch_defines_the_wrapper_it_calls():
    out = patch.patched_source(UPSTREAM)
    assert "def _wrap_with_session_context(coro):" in out


def test_the_patched_file_is_still_valid_python():
    ast.parse(patch.patched_source(UPSTREAM))


def test_the_patch_refuses_a_file_it_does_not_recognise():
    # A hermes-agent upgrade that reshapes _run_on_mcp_loop must stop the
    # build, not silently produce an unpatched image.
    with pytest.raises(patch.PatchError):
        patch.patched_source("def _run_on_mcp_loop(coro):\n    return coro\n")


def test_the_patch_refuses_an_ambiguous_file():
    with pytest.raises(patch.PatchError):
        patch.patched_source(UPSTREAM + "\n" + UPSTREAM)


def test_applying_the_patch_twice_changes_nothing():
    once = patch.patched_source(UPSTREAM)
    assert patch.patched_source(once) == once


# ── the injected wrapper's behaviour ─────────────────────

_UNSET = object()


class FakeSessionContext:
    """gateway.session_context, reduced to what the wrapper touches and
    faithful about the part that matters: a contextvar that was never set in
    this context falls through to a default, not to another thread's value."""

    def __init__(self):
        self._VAR_MAP = {
            name: ContextVar(name, default=_UNSET)
            for name in ("HERMES_SESSION_PLATFORM", "HERMES_SESSION_KEY", "HERMES_SESSION_CHAT_ID")
        }

    def get_session_env(self, name, default=""):
        var = self._VAR_MAP.get(name)
        if var is not None:
            value = var.get()
            if value is not _UNSET:
                return value
        return default

    def bind(self, **values):
        for name, value in values.items():
            self._VAR_MAP[name].set(value)


@pytest.fixture
def gateway(monkeypatch):
    fake = FakeSessionContext()
    monkeypatch.setitem(sys.modules, "gateway", type(sys)("gateway"))
    monkeypatch.setitem(sys.modules, "gateway.session_context", fake)
    return fake


def on_another_thread(coro):
    """Run `coro` on a thread of its own — a fresh context, exactly like the
    task run_coroutine_threadsafe creates inside the MCP loop thread."""
    box = {}
    thread = threading.Thread(target=lambda: box.update(result=asyncio.run(coro)))
    thread.start()
    thread.join()
    return box["result"]


def reads_the_session(gateway):
    async def _read():
        return (
            gateway.get_session_env("HERMES_SESSION_PLATFORM"),
            gateway.get_session_env("HERMES_SESSION_KEY"),
        )

    return _read()


def test_without_the_wrapper_the_session_is_lost_on_the_other_thread(gateway):
    # The bug being fixed: this is what every MCP tool call saw, which is why
    # an elicitation routed itself to the TTY-less CLI prompt and was denied.
    gateway.bind(HERMES_SESSION_PLATFORM="telegram", HERMES_SESSION_KEY="telegram:42")
    assert on_another_thread(reads_the_session(gateway)) == ("", "")


def test_the_wrapper_carries_the_session_onto_the_other_thread(gateway):
    gateway.bind(HERMES_SESSION_PLATFORM="telegram", HERMES_SESSION_KEY="telegram:42")
    wrapped = patch._wrap_with_session_context(reads_the_session(gateway))
    assert on_another_thread(wrapped) == ("telegram", "telegram:42")


def test_the_wrapper_leaves_a_session_free_call_alone(gateway):
    # CLI and one-shot runs bind no platform. Nothing to carry, nothing to
    # wrap — the coroutine must come back untouched.
    coro = reads_the_session(gateway)
    assert patch._wrap_with_session_context(coro) is coro
    coro.close()


def test_the_wrapper_gives_up_quietly_when_the_gateway_is_absent(monkeypatch):
    # Some hermes entrypoints have no gateway package at all. A patch that
    # raises here would break every MCP call instead of one approval prompt.
    monkeypatch.setitem(sys.modules, "gateway.session_context", None)

    async def _noop():
        return "ran"

    coro = _noop()
    assert patch._wrap_with_session_context(coro) is coro
    assert on_another_thread(coro) == "ran"


def test_the_default_target_is_the_installed_mcp_loop_module(monkeypatch):
    # Resolved from the interpreter's layout, never by importing hermes: this
    # runs during `docker build`, where importing the agent's tools package
    # would pull config and a HERMES_HOME that does not exist yet.
    monkeypatch.setattr(patch.sysconfig, "get_paths", lambda: {"purelib": "/x/site-packages"})
    assert patch._default_target() == Path("/x/site-packages/tools/mcp_tool_loop.py")
