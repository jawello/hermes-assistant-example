"""Tests for the patch that stops cron passes from queueing a recall.

A cron pass has one turn, so a recall queued after it serves nobody. Every other
platform must keep warming the context for its next turn.
"""
import threading

import pytest

import hindsight_cron_no_prefetch as patch

# Shaped like queue_prefetch in plugins/memory/hindsight/__init__.py, hindsight
# plugin 1.0.1 as pinned by hermes-agent 0.21.5.
PLUGIN = '''\
import logging
import threading

logger = logging.getLogger(__name__)


class HindsightMemoryProvider:
    def __init__(self, platform, recall_sync=False, disabled=False):
        self._platform = platform
        self._recall_sync = recall_sync
        self._disabled = disabled
        self._prefetch_thread = None
        self.recalled = []

    def _recall_disabled(self):
        return self._disabled

    def _do_recall(self, query):
        self.recalled.append(query)
        return "", 0

    def prefetch(self, query, *, session_id=""):
        if self._recall_sync:
            return self._do_recall(query)
        return ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        # Sync mode recalls live each turn — nothing to prime in the background.
        if self._recall_sync or self._recall_disabled():
            return

        def _run():
            self._do_recall(query)

        self._prefetch_thread = threading.Thread(target=_run)
        self._prefetch_thread.start()
'''

JOB_PROMPT = "[IMPORTANT: You are running as a scheduled cron job]"


def provider(source: str, platform: str, **kwargs):
    namespace = {}
    exec(compile(source, "__init__.py", "exec"), namespace)
    return namespace["HindsightMemoryProvider"](platform, **kwargs)


def queue(p) -> list[str]:
    p.queue_prefetch(JOB_PROMPT)
    if isinstance(p._prefetch_thread, threading.Thread):
        p._prefetch_thread.join(timeout=5)
    return p.recalled


def test_unpatched_cron_pass_queues_a_recall():
    # The bug this patch exists for: a recall nobody can read.
    assert queue(provider(PLUGIN, "cron")) == [JOB_PROMPT]


def test_cron_pass_queues_no_recall():
    p = provider(patch.patched_plugin(PLUGIN), "cron")
    assert queue(p) == []
    assert p._prefetch_thread is None


@pytest.mark.parametrize("platform", ["telegram", "cli"])
def test_other_platforms_still_queue_a_recall(platform):
    assert queue(provider(patch.patched_plugin(PLUGIN), platform)) == [JOB_PROMPT]


def test_disabled_recall_stays_disabled_on_other_platforms():
    assert queue(provider(patch.patched_plugin(PLUGIN), "telegram", disabled=True)) == []


def test_synchronous_recall_on_cron_is_untouched():
    # It is the one path that could give a pass memory, so the patch leaves it alone.
    p = provider(patch.patched_plugin(PLUGIN), "cron", recall_sync=True)
    p.prefetch(JOB_PROMPT)
    assert p.recalled == [JOB_PROMPT]


def test_patch_is_idempotent():
    once = patch.patched_plugin(PLUGIN)
    assert patch.patched_plugin(once) == once


def test_patch_refuses_an_unknown_plugin():
    with pytest.raises(patch.PatchError):
        patch.patched_plugin(PLUGIN.replace("self._recall_disabled():", "self._off():"))


def test_main_patches_the_file_in_place(tmp_path):
    plugin = tmp_path / "plugins" / "memory" / "hindsight" / "__init__.py"
    plugin.parent.mkdir(parents=True)
    plugin.write_text(PLUGIN)
    assert patch.main(["hindsight_cron_no_prefetch.py", str(tmp_path)]) == 0
    assert queue(provider(plugin.read_text(), "cron")) == []
