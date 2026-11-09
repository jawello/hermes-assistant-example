"""Tests for the tool search floor patch.

The patch is applied to a verbatim copy of hermes' tools/tool_search.py
(fixtures/), and the patched module runs with its hermes imports stubbed: a
surface below the floor must stay direct and say so in the log, a heavy one
must still be bridged, and with no floor the module must behave as upstream.
"""
import logging
import sys
import types
from pathlib import Path

import pytest

import tool_search_min_tokens as patch

UPSTREAM = (Path(__file__).parent / "fixtures" / "tool_search_v0.21.5.py").read_text()
BRIDGE = {"tool_search", "tool_describe", "tool_call"}


def module(name, **attrs):
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    return mod


@pytest.fixture
def stubs(monkeypatch):
    """hermes modules tool_search.py imports, reduced to what assembly touches."""
    connectors_in_scope = {"value": False}
    noop = lambda *a, **kw: None  # noqa: E731
    mods = {
        "hermes_cli": module("hermes_cli"),
        "hermes_cli.config_defaults": module(
            "hermes_cli.config_defaults",
            DEFAULT_CONFIG={"tools": {"tool_search": {"defer": ["todo_list"]}}}),
        "tools": module("tools"),
        "tools.registry": module("tools.registry", tool_error=noop),
        "tools.tool_search_catalog": module(
            "tools.tool_search_catalog",
            BRIDGE_TOOL_NAMES=frozenset(BRIDGE), CHARS_PER_TOKEN=4.0,
            TOOL_CALL_NAME="tool_call", TOOL_DESCRIBE_NAME="tool_describe",
            TOOL_SEARCH_NAME="tool_search", CatalogEntry=object,
            _fn=lambda td: td.get("function", {}), _listing_group_label=noop,
            _registry_entry=noop,
            _registry_toolset=lambda n: "mcp-telegram" if n.startswith("mcp__") else "terminal",
            build_catalog=noop,
            build_catalog_listing_with_form=lambda defs, max_tokens: ("listing", "full"),
            search_catalog=noop, hidden_declared_sources=noop),
        "tools.tool_search_validation": module(
            "tools.tool_search_validation", local_batch_error=noop,
            normalize_tool_call_entries=noop, not_deferrable_error=noop,
            validate_deferred_call_args=noop),
        "tools.connectors": module(
            "tools.connectors", CONNECTOR_BATCH_SENTINEL="*", is_connector_name=lambda n: False),
        "tools.connectors.search": module(
            "tools.connectors.search",
            connections_in_scope=lambda defs: connectors_in_scope["value"],
            connector_entries_by_group=noop, connectors_unavailable=noop,
            remote_schemas_for=noop),
        "toolsets": module("toolsets", _HERMES_CORE_TOOLS=["terminal"]),
        "snowballstemmer": module("snowballstemmer"),
    }
    for name, mod in mods.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return connectors_in_scope


def load(source, monkeypatch):
    # Registered first: the module's dataclass resolves its annotations there.
    mod = types.ModuleType("tool_search_under_test")
    monkeypatch.setitem(sys.modules, mod.__name__, mod)
    exec(compile(source, "tool_search.py", "exec"), mod.__dict__)
    return mod


@pytest.fixture
def ts(stubs, monkeypatch):
    return load(patch.patched_tool_search(UPSTREAM), monkeypatch)


def tools(count, tokens_each, prefix="mcp__telegram__t"):
    """`count` MCP tool defs of about `tokens_each` tokens (chars/4) each."""
    return [
        {"type": "function",
         "function": {"name": f"{prefix}{i}", "description": "x" * (tokens_each * 4),
                      "parameters": {"type": "object", "properties": {}}}}
        for i in range(count)
    ]


CORE = [{"type": "function", "function": {"name": "terminal", "description": "run",
                                          "parameters": {"type": "object", "properties": {}}}}]


def names(defs):
    return {d["function"]["name"] for d in defs}


def assemble(ts, defs, **raw):
    config = ts.ToolSearchConfig.from_raw({"enabled": "on", **raw})
    return ts.assemble_tool_defs(defs, context_length=1_000_000, config=config)


def test_a_light_surface_stays_direct_and_says_so(ts, caplog):
    defs = CORE + tools(13, 220)  # ~2.9K tokens, a pinned Telegram job
    with caplog.at_level(logging.INFO):
        out = assemble(ts, defs, min_deferred_tokens=8000)
    assert not out.activated
    assert names(out.tool_defs) == names(defs)
    assert "tool_search kept eager: 13 deferrable tools (~" in caplog.text
    assert "below min_deferred_tokens 8000" in caplog.text


def test_a_heavy_surface_is_still_bridged(ts):
    defs = CORE + tools(190, 220)  # ~42K tokens, chat
    out = assemble(ts, defs, min_deferred_tokens=8000)
    assert out.activated
    assert BRIDGE <= names(out.tool_defs)
    assert not any(n.startswith("mcp__") for n in names(out.tool_defs))


def test_a_surface_exactly_at_the_floor_is_bridged(ts):
    defs = CORE + tools(1, 1)
    tokens = ts.estimate_tokens_from_schemas(defs[1:])
    assert assemble(ts, defs, min_deferred_tokens=tokens).activated


def test_without_a_floor_upstream_behaviour_is_unchanged(ts):
    defs = CORE + tools(13, 220)
    assert assemble(ts, defs).activated
    assert assemble(ts, defs, min_deferred_tokens=0).activated


@pytest.mark.parametrize("raw, expected", [
    (8000, 8000), ("8000", 8000), ("lots", 0), (-5, 0), (None, 0), (10**9, 1_000_000)])
def test_the_floor_is_read_and_clamped(ts, raw, expected):
    config = ts.ToolSearchConfig.from_raw({"enabled": "on", "min_deferred_tokens": raw})
    assert config.min_deferred_tokens == expected


def test_connectors_in_scope_still_turn_the_bridge_on(ts, stubs):
    stubs["value"] = True
    assert assemble(ts, CORE + tools(13, 220), min_deferred_tokens=8000).activated


def test_off_is_not_reported_as_kept_eager(ts, caplog):
    config = ts.ToolSearchConfig.from_raw({"enabled": "off", "min_deferred_tokens": 8000})
    with caplog.at_level(logging.INFO):
        out = ts.assemble_tool_defs(CORE + tools(13, 220), context_length=1_000_000, config=config)
    assert not out.activated
    assert "kept eager" not in caplog.text


def test_patching_twice_changes_nothing():
    once = patch.patched_tool_search(UPSTREAM)
    assert patch.patched_tool_search(once) == once


def test_a_file_without_an_anchor_is_refused():
    with pytest.raises(patch.PatchError):
        patch.patched_tool_search(UPSTREAM.replace("    return connections_granted\n", ""))


def test_a_file_with_an_anchor_twice_is_refused():
    with pytest.raises(patch.PatchError):
        patch.patched_tool_search(UPSTREAM + UPSTREAM)


def test_a_partly_patched_file_is_refused():
    once = patch.patched_tool_search(UPSTREAM)
    with pytest.raises(patch.PatchError):
        patch.patched_tool_search(once.replace("    min_deferred_tokens: int = 0\n", ""))


def test_main_patches_the_file_in_place(tmp_path):
    target = tmp_path / "tools" / "tool_search.py"
    target.parent.mkdir()
    target.write_text(UPSTREAM)
    assert patch.main(["prog", str(tmp_path)]) == 0
    assert patch.MARK in target.read_text()
