#!/usr/bin/env python3
"""Carry the gateway session into MCP calls, so a server can ask the user.

**The bug.** `tools/mcp_tool_loop.py:_run_on_mcp_loop` schedules every MCP call
with `run_coroutine_threadsafe`. That creates the task inside the MCP loop
thread, so the task copies *that* thread's context — the agent's
`HERMES_SESSION_*` contextvars are not in it. Upstream knows this and
re-establishes the `HERMES_HOME` override and the dashboard OAuth flow for
exactly this reason (`_wrap_with_home_override`,
`_wrap_with_dashboard_oauth_flow`), but not the session vars. 0.21 snapshots
the context for elicitation in the tool handler, but that snapshot is taken
inside the MCP task, after the session vars are already gone.

Anything that reads the session from inside an MCP call therefore sees an
unbound one. For `elicitation/create` that is load-bearing:
`tools/approval.py:_is_gateway_approval_context()` reads
`HERMES_SESSION_PLATFORM`, finds it empty, and routes the approval to the CLI
reader instead of Telegram. In a container that reader has no TTY, `input()`
hits EOF, and the default answer is deny — so gmail-mcp's send gate returned
`not confirmed (decline)` on every attempt and no mail could ever be sent.
Verified on 2026-09-21 against hermes-agent 0.18.0; 0.21.5 has the same gap
(the helper moved from `tools/mcp_tool.py` to `tools/mcp_tool_loop.py`).

**The patch.** One call, right after upstream's own wrappers and before the
coroutine is scheduled, in the same shape as those wrappers. Applied at image build time against the
installed package. It refuses to guess: if `_run_on_mcp_loop` no longer looks
like this, the build fails and a human re-checks it, rather than an unpatched
image shipping quietly.

Usage: `python mcp_session_context.py [path/to/tools/mcp_tool_loop.py]`
"""
import inspect
import sys
import sysconfig
from pathlib import Path


class PatchError(Exception):
    """The target file is not the one this patch was written against."""


# The call site: upstream's own wrapping statement, which the injected call
# goes after.
ANCHOR = (
    "    coro = _wrap_with_dashboard_oauth_flow(_wrap_with_home_override(\n"
    "        coro_or_factory() if callable(coro_or_factory) else coro_or_factory))\n"
)
ADDED = "    coro = _wrap_with_session_context(coro)\n"
DEFINITION_ANCHOR = "def _run_on_mcp_loop("


def _wrap_with_session_context(coro):
    """Carry the caller's gateway session vars into ``coro``.

    Returns ``coro`` unchanged when there is no session to carry — CLI and
    one-shot runs bind no platform, and cron's approvals are governed by
    ``approvals.cron_mode``, not by this. Otherwise wraps it so the vars are
    rebound inside the coroutine's own (task-local) context on the MCP loop
    and reset when it completes, so concurrent calls carrying different
    sessions don't interfere.
    """
    try:
        from gateway.session_context import _VAR_MAP, get_session_env
    except Exception:
        return coro
    captured = {name: get_session_env(name, "") for name in _VAR_MAP}
    if not captured.get("HERMES_SESSION_PLATFORM"):
        return coro

    async def _scoped():
        tokens = [
            (_VAR_MAP[name], _VAR_MAP[name].set(value))
            for name, value in captured.items()
            if value
        ]
        try:
            return await coro
        finally:
            for var, token in reversed(tokens):
                var.reset(token)

    return _scoped()


def patched_source(source: str) -> str:
    """`source` with the session-context wrapper defined and called.

    Idempotent: already-patched source comes back unchanged.
    """
    if ADDED in source:
        return source
    if source.count(ANCHOR) != 1:
        raise PatchError(
            "expected exactly one upstream wrapping statement in _run_on_mcp_loop, "
            f"found {source.count(ANCHOR)} — hermes-agent's MCP scheduling "
            "has changed and this patch must be re-checked"
        )
    if source.count(DEFINITION_ANCHOR) != 1:
        raise PatchError(
            f"expected exactly one {DEFINITION_ANCHOR!r}, "
            f"found {source.count(DEFINITION_ANCHOR)}"
        )
    source = source.replace(ANCHOR, ANCHOR + ADDED, 1)
    # The function is injected from its own source here, so the code the
    # tests exercise and the code the image runs are the same text.
    definition = inspect.getsource(_wrap_with_session_context)
    return source.replace(DEFINITION_ANCHOR, definition + "\n\n" + DEFINITION_ANCHOR, 1)


def _default_target() -> Path:
    """Where pip put hermes-agent's MCP tool module.

    Resolved from the interpreter's own layout rather than by importing
    `tools.mcp_tool_loop`: this runs during `docker build`, and importing the
    agent's tools package there would pull in config and a HERMES_HOME that
    does not exist yet.
    """
    return Path(sysconfig.get_paths()["purelib"]) / "tools" / "mcp_tool_loop.py"


def main(argv: list[str]) -> int:
    target = Path(argv[1]) if len(argv) > 1 else _default_target()
    source = target.read_text()
    patched = patched_source(source)
    if patched == source:
        print(f"{target}: already patched")
        return 0
    target.write_text(patched)
    print(f"{target}: session context now carried onto the MCP loop")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
