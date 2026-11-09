"""Tests for the usage_audit fields patch.

The patch is applied to a verbatim copy of hermes' _FireAudit and the run_job
line that builds it (fixtures/), and the patched code runs with the
scheduler's helpers and hermes' tool search stubbed: each line must carry the
new fields for a bridged and a direct agent, and must still be written, with
nulls, when a field cannot be computed.
"""
import json
import sys
import time
import types
import uuid
from pathlib import Path
from typing import Optional

import pytest

import cron_usage_audit_fields as patch

UPSTREAM = (Path(__file__).parent / "fixtures" / "fire_audit_v0.21.5.py").read_text()
BRIDGE = frozenset({"tool_search", "tool_describe", "tool_call"})


def tool(name, chars=400):
    return {"type": "function", "function": {"name": name, "description": "x" * chars}}


TELEGRAM = [tool(f"mcp__telegram__t{i}") for i in range(3)]


@pytest.fixture
def hermes(monkeypatch):
    """tools.tool_search and model_tools as the audit reads them."""
    state = {"raw": [tool("terminal")] + TELEGRAM, "floor": 8000, "raw_error": None}

    def get_tool_definitions(enabled_toolsets=None, disabled_toolsets=None,
                             quiet_mode=False, skip_tool_search_assembly=False):
        assert skip_tool_search_assembly and quiet_mode
        if state["raw_error"]:
            raise state["raw_error"]
        return state["raw"]

    def classify_tools(defs, defer_tools=None):
        visible = [d for d in defs if not d["function"]["name"].startswith("mcp__")]
        return visible, [d for d in defs if d["function"]["name"].startswith("mcp__")]

    tool_search = types.ModuleType("tools.tool_search")
    tool_search.BRIDGE_TOOL_NAMES = BRIDGE
    tool_search.classify_tools = classify_tools
    tool_search.estimate_tokens_from_schemas = lambda defs: sum(
        len(json.dumps(d)) for d in defs) // 4
    tool_search.load_config_readonly = lambda: types.SimpleNamespace(
        effective_defer_tools=frozenset(), min_deferred_tokens=state["floor"])
    tools_pkg = types.ModuleType("tools")
    tools_pkg.tool_search = tool_search
    model_tools = types.ModuleType("model_tools")
    model_tools.get_tool_definitions = get_tool_definitions
    monkeypatch.setitem(sys.modules, "tools", tools_pkg)
    monkeypatch.setitem(sys.modules, "tools.tool_search", tool_search)
    monkeypatch.setitem(sys.modules, "model_tools", model_tools)
    return state


@pytest.fixture
def scheduler(hermes):
    written = []
    namespace = {
        "json": json, "time": time, "uuid": uuid, "Optional": Optional,
        "_write_usage_audit": written.append,
        "_utcnow_iso_ms": lambda: "2026-10-05T19:31:22.848Z",
    }
    exec(compile(patch.patched_scheduler(UPSTREAM), "scheduler.py", "exec"), namespace)
    namespace["written"] = written
    return namespace


JOB = {"id": "29e908ad9a6a", "name": "telegram-hourly", "deliver": "telegram"}


def agent(tools):
    return types.SimpleNamespace(tools=tools, enabled_toolsets=["terminal", "telegram"],
                                 disabled_toolsets=None)


def assistant(*names):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"id": f"c{i}", "type": "function",
                            "function": {"name": n, "arguments": "{}"}}
                           for i, n in enumerate(names)]}


def tool_result(content):
    return {"role": "tool", "tool_call_id": "c0", "content": content}


RESULT = {
    "prompt_tokens": 70000, "completion_tokens": 900, "total_tokens": 70900,
    "api_calls": 5, "cache_read_tokens": 61000,
    "messages": [
        {"role": "system", "content": "..."},
        {"role": "user", "content": "Run pass-window telegram start."},
        assistant("terminal"),
        # hermes' terminal answers JSON with "error": null on success.
        tool_result('{"output": "since=2026-10-05T18:30:23Z", "exit_code": 0, "error": null}'),
        assistant("mcp__telegram__list_chats"),
        tool_result('{"results": []}'),
        assistant(*["mcp__telegram__list_messages"] * 3),
        tool_result('{"error": "tool_call takes exactly one entry for local tools"}'),
        tool_result('{"output": "", "exit_code": 0, "error": ""}'),
        tool_result("-ERR- upstream text error, not JSON"),
        tool_result([{"type": "text", "text": "multimodal"}]),
        {"role": "assistant", "content": "[SILENT]"},
    ],
}


def fire(scheduler, the_agent, result=RESULT, error=None):
    audit = scheduler["_run_job_call_site"](JOB, JOB["id"], "openai/gpt-6-luna", the_agent)
    audit.write(result, error)
    assert len(scheduler["written"]) == 1
    return scheduler["written"][0]


def test_upstream_fields_are_kept(scheduler):
    line = fire(scheduler, agent([tool("terminal")] + TELEGRAM))
    assert line["job_id"] == "29e908ad9a6a"
    assert line["prompt_tokens"] == 70000
    assert line["deliver_target"] == "telegram"
    assert line["error"] is None


def test_a_direct_run_is_recorded(scheduler):
    defs = [tool("terminal")] + TELEGRAM
    line = fire(scheduler, agent(defs))
    assert line["job_name"] == "telegram-hourly"
    assert line["api_calls"] == 5
    assert line["cache_read_tokens"] == 61000
    assert line["tool_calls"] == {"mcp__telegram__list_chats": 1,
                                  "mcp__telegram__list_messages": 3, "terminal": 1}
    assert line["tool_errors"] == 1
    surface = line["tool_surface"]
    assert surface["bridged"] is False
    assert surface["schema_tokens"] == sum(len(json.dumps(d)) for d in defs) // 4
    assert surface["deferrable_tokens"] == sum(len(json.dumps(d)) for d in TELEGRAM) // 4
    assert surface["min_deferred_tokens"] == 8000


def test_a_bridged_run_is_recorded(scheduler):
    defs = [tool("terminal"), tool("tool_search"), tool("tool_describe"), tool("tool_call")]
    result = dict(RESULT, messages=[assistant("tool_search"), assistant("tool_search"),
                                    assistant("tool_describe"), assistant("tool_call")])
    line = fire(scheduler, agent(defs), result)
    assert line["tool_surface"]["bridged"] is True
    assert line["tool_calls"] == {"tool_call": 1, "tool_describe": 1, "tool_search": 2}


def test_a_failing_field_is_null_and_the_line_is_still_written(scheduler, hermes):
    hermes["raw_error"] = RuntimeError("registry gone")
    line = fire(scheduler, agent([tool("terminal")] + TELEGRAM))
    assert line["tool_surface"]["deferrable_tokens"] is None
    assert line["tool_surface"]["bridged"] is False
    assert line["job_name"] == "telegram-hourly"


def test_garbage_messages_do_not_break_the_line(scheduler):
    result = dict(RESULT, messages=[None, "text", {"role": "assistant", "tool_calls": [None]}])
    line = fire(scheduler, agent([]), result)
    assert line["tool_calls"] == {}
    assert line["tool_errors"] == 0


def test_a_fire_that_failed_before_the_run_still_gets_its_name(scheduler):
    line = fire(scheduler, agent([tool("terminal")]), result={}, error="RuntimeError: boom")
    assert line["error"] == "RuntimeError: boom"
    assert line["job_name"] == "telegram-hourly"
    assert line["api_calls"] is None
    assert line["tool_calls"] == {}


def test_an_audit_without_an_agent_has_no_surface(scheduler):
    audit = scheduler["_FireAudit"](JOB, JOB["id"], "m")
    audit.write(RESULT, None)
    assert scheduler["written"][0]["tool_surface"] is None


def test_patching_twice_changes_nothing():
    once = patch.patched_scheduler(UPSTREAM)
    assert patch.patched_scheduler(once) == once


def test_a_file_without_the_call_site_is_refused():
    with pytest.raises(patch.PatchError):
        patch.patched_scheduler(UPSTREAM.replace("        _audit = _FireAudit(job, job_id, model)\n", ""))


def test_a_file_with_an_anchor_twice_is_refused():
    with pytest.raises(patch.PatchError):
        patch.patched_scheduler(UPSTREAM + UPSTREAM)


def test_a_partly_patched_file_is_refused():
    once = patch.patched_scheduler(UPSTREAM)
    with pytest.raises(patch.PatchError):
        patch.patched_scheduler(once.replace("agent=agent)", ")"))


def test_main_patches_the_file_in_place(tmp_path):
    target = tmp_path / "cron" / "scheduler.py"
    target.parent.mkdir()
    target.write_text(UPSTREAM)
    assert patch.main(["prog", str(tmp_path)]) == 0
    assert patch.MARK in target.read_text()
