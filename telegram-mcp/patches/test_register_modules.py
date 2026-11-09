"""Tests for the patch that registers our own tool modules (the send gate and
the history search).

It must add one import of each module to upstream's `tools/__init__.py`, after
the last upstream tool module and before `__all__`, and refuse a file it does
not recognise.
"""
from pathlib import Path

import pytest

import register_modules as patch

UPSTREAM = (Path(__file__).parent / "fixtures" / "tools_init_v3.2.65.py").read_text()
IMPORTS = ("from telegram_mcp.tools.send_gate.tools import *\n",
           "from telegram_mcp.tools.history_search.tools import *\n")


def test_the_imports_land_after_the_last_tool_module_and_before_all():
    out = patch.patched_init(UPSTREAM)
    for line in IMPORTS:
        assert out.count(line) == 1
        assert out.index("tools.events import *") < out.index(line) < out.index("__all__")


def test_patching_twice_changes_nothing():
    once = patch.patched_init(UPSTREAM)
    assert patch.patched_init(once) == once


def test_a_file_without_the_anchor_is_refused():
    with pytest.raises(patch.PatchError):
        patch.patched_init(UPSTREAM.replace("from telegram_mcp.tools.events import *\n", ""))


def test_a_file_without_all_is_refused():
    with pytest.raises(patch.PatchError):
        patch.patched_init(UPSTREAM.replace("__all__", "__exported__"))


def test_main_patches_the_file_in_place(tmp_path):
    target = tmp_path / "__init__.py"
    target.write_text(UPSTREAM)
    assert patch.main(["x", str(target)]) == 0
    assert all(line in target.read_text() for line in IMPORTS)
