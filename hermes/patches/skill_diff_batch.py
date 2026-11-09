#!/usr/bin/env python3
"""Show what a staged skill batch changes in /skills diff.

**The bug.** `tools/write_approval.py:skill_pending_diff` renders create,
edit, patch and write_file, and anything else as "(<action> on '<name>')".
The bot stages a rule as `skill_manage` batch, so `/skills diff` answered
"(batch on '')" for the first rule under the approval gate (cf2abb26,
2026-11-06), and the user would approve a change he could not see.

**The fix.** A batch renders every file it touches as one unified diff:
the on-disk text before, every operation of the batch applied in order
after, so a patch that matches an earlier patch's text shows too. A patch
that matches nothing is said; other operations (create, delete, ...) use
the single-write renderer.

The patch fails the build if the renderer changes shape, so an unpatched
image cannot ship quietly.

Usage: `python skill_diff_batch.py <hermes source dir>`
"""
import sys
from pathlib import Path


class PatchError(Exception):
    """The target file is not the one this patch was written against."""


ANCHOR = '    if action not in {"edit", "patch", "write_file"}:\n'
BRANCH = (
    '    if action == "batch":\n'
    "        # Patched in by personal-assistant's skill_diff_batch.py: upstream showed\n"
    "        # a batch as \"(batch on '')\", so a staged rule could not be reviewed.\n"
    '        return _batch_pending_diff(payload.get("operations") or [])\n'
)
HELPER = '''

# Patched in by personal-assistant's skill_diff_batch.py.
def _batch_pending_diff(operations):
    """Each file a batch touches as one diff: on disk before, all operations applied after."""
    files, notes = {}, []
    for op in operations:
        action, name = op.get("action", ""), op.get("name", "")
        if action not in {"edit", "patch", "write_file"}:
            notes.append(skill_pending_diff({"payload": op}))
            continue
        label = "SKILL.md" if action == "edit" else (op.get("file_path") or "SKILL.md")
        if (name, label) not in files:
            current = ""
            skill_dir = _find_skill_path(name)
            if skill_dir:
                with suppress(Exception):
                    p = skill_dir / label
                    current = p.read_text(encoding="utf-8") if p.exists() else ""
            files[(name, label)] = [current, current]
        text = files[(name, label)][1]
        if action == "patch":
            old_s, new_s = op.get("old_string") or "", op.get("new_string") or ""
            if not old_s or old_s not in text:
                notes.append(f"patch on {name}/{label} does not match: {old_s[:200]!r}")
                continue
            text = text.replace(old_s, new_s)
        else:
            text = op.get("content" if action == "edit" else "file_content") or ""
        files[(name, label)][1] = text
    diffs = ["".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"a/{name}/{label}", tofile=f"b/{name}/{label}"))
        for (name, label), (before, after) in files.items()]
    return "\\n".join(part for part in notes + diffs if part) or "(no textual change)"
'''


def patched_module(source: str) -> str:
    """write_approval.py whose skill_pending_diff renders a batch."""
    if BRANCH in source:
        return source
    found = source.count(ANCHOR)
    if found != 1:
        raise PatchError(
            "expected exactly one single-write action check in "
            f"tools/write_approval.py:skill_pending_diff, found {found} — upstream "
            "changed the renderer; re-check this patch"
        )
    return source.replace(ANCHOR, BRANCH + ANCHOR, 1).rstrip("\n") + "\n" + HELPER


def main(argv: list[str]) -> int:
    target = Path(argv[1]) / "tools" / "write_approval.py"
    source = target.read_text()
    patched = patched_module(source)
    if patched == source:
        print(f"{target}: already patched")
    else:
        target.write_text(patched)
        print(f"{target}: /skills diff now renders batch writes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
