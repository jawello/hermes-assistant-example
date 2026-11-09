"""Tests for the patch that makes cron turns retain only the reply.

A cron turn must reach memory without the job's prompt, a silent pass must not
reach memory at all, and a chat turn must stay exactly as upstream stores it.
"""
import json
import sys
import types

import pytest

import hindsight_cron_reply_only as patch

# Shaped like sync_turn in plugins/memory/hindsight/__init__.py, hindsight
# plugin 1.0.1 as pinned by hermes-agent 0.21.5.
PLUGIN = '''\
import json
import logging

logger = logging.getLogger(__name__)


class HindsightMemoryProvider:
    def __init__(self, platform):
        self._platform = platform
        self._session_turns = []
        self._turn_counter = 0

    def _build_turn_messages(self, user_content, assistant_content):
        return [
            {"role": "user", "content": f"User: {user_content}"},
            {"role": "assistant", "content": f"Assistant: {assistant_content}"},
        ]

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        if session_id:
            self._session_id = str(session_id).strip()

        self._session_turns.append(
            json.dumps(self._build_turn_messages(user_content, assistant_content), ensure_ascii=False)
        )
        self._turn_counter = self._turn_index = self._turn_counter + 1
'''

JOB_PROMPT = "Для двухчасового прохода вызови list_chats и сохрани новые факты"


@pytest.fixture(autouse=True)
def silence_filter(monkeypatch):
    # The real check lives in hermes core, which these tests do not install.
    filters = types.ModuleType("gateway.response_filters")
    filters.is_autonomous_silence_response = lambda text: text.strip().upper() == "[SILENT]"
    monkeypatch.setitem(sys.modules, "gateway", types.ModuleType("gateway"))
    monkeypatch.setitem(sys.modules, "gateway.response_filters", filters)


def provider(source: str, platform: str):
    namespace = {}
    exec(compile(source, "__init__.py", "exec"), namespace)
    return namespace["HindsightMemoryProvider"](platform)


def stored_roles(p) -> list[list[str]]:
    return [[m["role"] for m in json.loads(turn)] for turn in p._session_turns]


def test_unpatched_cron_turn_stores_the_job_prompt():
    # The bug this patch exists for: extraction retold the prompt as facts.
    p = provider(PLUGIN, "cron")
    p.sync_turn(JOB_PROMPT, "Письмо от банка: карта готова")
    assert JOB_PROMPT in p._session_turns[0]


def test_cron_turn_stores_only_the_reply():
    p = provider(patch.patched_plugin(PLUGIN), "cron")
    p.sync_turn(JOB_PROMPT, "Письмо от банка: карта готова")
    assert stored_roles(p) == [["assistant"]]
    assert JOB_PROMPT not in p._session_turns[0]
    assert "карта готова" in p._session_turns[0]


@pytest.mark.parametrize("reply", ["[SILENT]", " [silent]\n", "", "  \n"])
def test_silent_or_empty_cron_turn_stores_nothing(reply):
    p = provider(patch.patched_plugin(PLUGIN), "cron")
    p.sync_turn(JOB_PROMPT, reply)
    assert p._session_turns == []
    assert p._turn_counter == 0


@pytest.mark.parametrize("platform", ["telegram", "cli"])
def test_other_platforms_keep_both_messages(platform):
    p = provider(patch.patched_plugin(PLUGIN), platform)
    p.sync_turn("Какой номер машины?", "[SILENT]")
    assert stored_roles(p) == [["user", "assistant"]]


def test_patch_is_idempotent():
    once = patch.patched_plugin(PLUGIN)
    assert patch.patched_plugin(once) == once


def test_patch_refuses_an_unknown_plugin():
    with pytest.raises(patch.PatchError):
        patch.patched_plugin(PLUGIN.replace("self._session_turns.append(", "self._turns.append("))


def test_main_patches_the_file_in_place(tmp_path):
    plugin = tmp_path / "plugins" / "memory" / "hindsight" / "__init__.py"
    plugin.parent.mkdir(parents=True)
    plugin.write_text(PLUGIN)
    assert patch.main(["hindsight_cron_reply_only.py", str(tmp_path)]) == 0
    p = provider(plugin.read_text(), "cron")
    p.sync_turn(JOB_PROMPT, "Письмо от банка")
    assert stored_roles(p) == [["assistant"]]
