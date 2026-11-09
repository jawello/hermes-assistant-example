"""Tests for the hindsight tool-result UTF-8 patch.

The patch must rewrite a file shaped like upstream's, refuse one it does not
recognise, and make a Russian recall reach the model as Russian text while
staying the same JSON.
"""
import json

import pytest

import hindsight_tool_utf8 as patch

# Shaped like handle_tool_call in plugins/memory/hindsight/__init__.py,
# hermes-agent 0.21.5.
PLUGIN = '''\
import json


def tool_error(message):
    return json.dumps({"error": message})


class HindsightMemoryProvider:
    _TOOL_HANDLERS = {
        "hindsight_recall": ("query", lambda self, args: "1. пользователь смотрит фильмы в Kodi", "Recall failed"),
    }

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        if tool_name not in self._TOOL_HANDLERS:
            return tool_error(f"Unknown tool: {tool_name}")
        required, handler, failure = self._TOOL_HANDLERS[tool_name]
        if not args.get(required, ""):
            return tool_error(f"Missing required parameter: {required}")
        try:
            return json.dumps({"result": handler(self, args)})
        except Exception as e:
            return tool_error(f"{failure}: {e}")
'''


def recall(source: str) -> str:
    namespace = {}
    exec(compile(source, "__init__.py", "exec"), namespace)
    return namespace["HindsightMemoryProvider"]().handle_tool_call("hindsight_recall", {"query": "Kodi"})


def test_unpatched_plugin_escapes_cyrillic():
    # The bug this patch exists for: every Cyrillic letter became \\uXXXX.
    assert "\\u043f" in recall(PLUGIN)


def test_patched_plugin_keeps_cyrillic():
    out = recall(patch.patched_plugin(PLUGIN))
    assert "пользователь смотрит фильмы" in out
    assert "\\u" not in out


def test_patched_result_is_the_same_json():
    assert json.loads(recall(patch.patched_plugin(PLUGIN))) == json.loads(recall(PLUGIN))


def test_patch_is_idempotent():
    once = patch.patched_plugin(PLUGIN)
    assert patch.patched_plugin(once) == once


def test_patch_refuses_an_unknown_plugin():
    with pytest.raises(patch.PatchError):
        patch.patched_plugin(PLUGIN.replace('json.dumps({"result"', 'json.dumps({"results"'))


def test_main_patches_the_file_in_place(tmp_path):
    plugin = tmp_path / "plugins" / "memory" / "hindsight" / "__init__.py"
    plugin.parent.mkdir(parents=True)
    plugin.write_text(PLUGIN)
    assert patch.main(["hindsight_tool_utf8.py", str(tmp_path)]) == 0
    assert "пользователь" in recall(plugin.read_text())
