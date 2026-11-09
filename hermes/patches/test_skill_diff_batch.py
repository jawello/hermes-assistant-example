"""Tests for the /skills diff batch patch.

The bot stages a rule as `skill_manage` batch, and upstream's renderer shows
such a write as "(batch on '')" (cf2abb26, 2026-11-06): the user approves
blind. Patched, every file the batch touches is one unified diff, with the
operations applied in order.
"""
import pytest

import skill_diff_batch as patch

# Shaped like skill_pending_diff in tools/write_approval.py, hermes-agent 0.21.5.
MODULE = '''\
import difflib
from contextlib import suppress
from pathlib import Path

SKILLS_ROOT = None


def _find_skill_path(name):
    p = SKILLS_ROOT / name
    return p if p.is_dir() else None


def skill_pending_diff(record):
    """Full content (create) or unified diff vs. the on-disk skill (edit/patch/write_file),
    rendered by /skills diff <id> on surfaces that can show it."""
    payload = record.get("payload", {})
    action = payload.get("action", "")
    name = payload.get("name", "")
    if action == "create":
        return payload.get("content") or ""
    if action not in {"edit", "patch", "write_file"}:
        return {"remove_file": f"remove file: {payload.get('file_path')} from skill '{name}'",
                "delete": f"delete skill '{name}'"}.get(action, f"({action} on '{name}')")

    # patch/write_file target a file inside the skill; edit always targets SKILL.md.
    target_label, current = "SKILL.md", ""
    skill_dir = _find_skill_path(name)
    if skill_dir:
        if action != "edit":
            target_label = payload.get("file_path") or "SKILL.md"
        with suppress(Exception):
            p = skill_dir / target_label
            current = p.read_text(encoding="utf-8") if p.exists() else ""

    if action == "patch":
        old_s, new_s = payload.get("old_string") or "", payload.get("new_string") or ""
        new = current.replace(old_s, new_s) if current else f"(patch {old_s!r} → {new_s!r})"
    else:
        new = payload.get("content" if action == "edit" else "file_content") or ""
    diff = difflib.unified_diff(current.splitlines(keepends=True), new.splitlines(keepends=True),
                                fromfile=f"a/{target_label}", tofile=f"b/{target_label}")
    return "".join(diff) or "(no textual change)"
'''


@pytest.fixture
def skills(tmp_path):
    for name, text in {"ticktick": "# TickTick\n- rule one\n- rule two\n",
                       "trakt": "# Trakt\n- old\n"}.items():
        (tmp_path / name).mkdir()
        (tmp_path / name / "SKILL.md").write_text(text)
    return tmp_path


def render(source, skills_root, operations):
    namespace = {}
    exec(compile(source, "write_approval.py", "exec"), namespace)
    namespace["SKILLS_ROOT"] = skills_root
    record = {"payload": {"action": "batch", "operations": operations}}
    return namespace["skill_pending_diff"](record)


def op(name, old, new):
    return {"name": name, "action": "patch", "old_string": old, "new_string": new}


def test_unpatched_batch_shows_nothing(skills):
    # The bug: a batch is rendered as its action name only.
    assert render(MODULE, skills, [op("ticktick", "- rule two", "- rule 2")]) == "(batch on '')"


def test_patched_batch_shows_the_diff(skills):
    out = render(patch.patched_module(MODULE), skills, [op("ticktick", "- rule two", "- rule 2")])
    assert "--- a/ticktick/SKILL.md" in out
    assert "-- rule two\n" in out and "+- rule 2\n" in out


def test_operations_on_one_file_apply_in_order(skills):
    # The second patch matches text the first one wrote; diffed against the
    # disk alone it would look like no change.
    out = render(patch.patched_module(MODULE), skills,
                 [op("ticktick", "- rule two", "- rule 2"), op("ticktick", "- rule 2", "- rule II")])
    assert "+- rule II\n" in out
    assert out.count("--- a/ticktick/SKILL.md") == 1


def test_each_skill_gets_its_own_diff(skills):
    out = render(patch.patched_module(MODULE), skills,
                 [op("ticktick", "- rule one", "- rule 1"), op("trakt", "- old", "- new")])
    assert "--- a/ticktick/SKILL.md" in out and "--- a/trakt/SKILL.md" in out
    assert "+- new\n" in out


def test_a_patch_that_does_not_match_is_said(skills):
    out = render(patch.patched_module(MODULE), skills, [op("ticktick", "- missing", "- x")])
    assert "does not match" in out and "ticktick" in out


def test_other_operations_use_the_single_renderer(skills):
    out = render(patch.patched_module(MODULE), skills,
                 [{"name": "trakt", "action": "delete"}, op("ticktick", "- rule one", "- rule 1")])
    assert "delete skill 'trakt'" in out
    assert "+- rule 1\n" in out


def test_single_writes_are_unchanged(skills):
    namespace = {}
    exec(compile(patch.patched_module(MODULE), "write_approval.py", "exec"), namespace)
    namespace["SKILLS_ROOT"] = skills
    record = {"payload": op("ticktick", "- rule one", "- rule 1") | {"action": "patch"}}
    assert "+- rule 1\n" in namespace["skill_pending_diff"](record)


def test_patch_is_idempotent():
    once = patch.patched_module(MODULE)
    assert patch.patched_module(once) == once


def test_patch_refuses_an_unknown_module():
    with pytest.raises(patch.PatchError):
        patch.patched_module(MODULE.replace('if action not in {"edit", "patch", "write_file"}:',
                                            'if action not in {"edit", "patch"}:'))


def test_main_patches_the_file_in_place(tmp_path, skills):
    target = tmp_path / "src" / "tools" / "write_approval.py"
    target.parent.mkdir(parents=True)
    target.write_text(MODULE)
    assert patch.main(["skill_diff_batch.py", str(tmp_path / "src")]) == 0
    assert "+- rule 2\n" in render(target.read_text(), skills, [op("ticktick", "- rule two", "- rule 2")])
