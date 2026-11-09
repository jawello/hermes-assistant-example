"""Tests for the external prefetch timeout patch.

The patch must raise the timeout in a file shaped like upstream's, refuse one it
does not recognise, and leave an explicit constructor argument in charge.
"""
import pytest

import hindsight_prefetch_timeout as patch

# Shaped like agent/memory_manager.py, hermes-agent 0.21.5.
MANAGER = '''\
from typing import Optional

_EXTERNAL_PREFETCH_TIMEOUT_S = 8.0


class MemoryManager:
    def __init__(self, *, external_prefetch_timeout: Optional[float] = None) -> None:
        timeout = external_prefetch_timeout
        timeout = _EXTERNAL_PREFETCH_TIMEOUT_S if timeout is None else float(timeout)
        if timeout <= 0:
            raise ValueError("external_prefetch_timeout must be positive")
        self._external_prefetch_timeout = timeout
'''


def timeout(source: str, **kwargs) -> float:
    namespace = {}
    exec(compile(source, "memory_manager.py", "exec"), namespace)
    return namespace["MemoryManager"](**kwargs)._external_prefetch_timeout


def test_unpatched_manager_waits_8_seconds():
    assert timeout(MANAGER) == 8.0


def test_patched_manager_waits_15_seconds():
    assert timeout(patch.patched_manager(MANAGER)) == 15.0


def test_explicit_argument_still_wins():
    assert timeout(patch.patched_manager(MANAGER), external_prefetch_timeout=3) == 3.0


def test_patch_is_idempotent():
    once = patch.patched_manager(MANAGER)
    assert patch.patched_manager(once) == once


def test_patch_refuses_a_changed_default():
    with pytest.raises(patch.PatchError):
        patch.patched_manager(MANAGER.replace("= 8.0", "= 10.0"))


def test_patch_refuses_a_manager_that_ignores_the_constant():
    changed = MANAGER.replace(
        "timeout = _EXTERNAL_PREFETCH_TIMEOUT_S if timeout is None else float(timeout)",
        "timeout = 8.0 if timeout is None else float(timeout)",
    )
    with pytest.raises(patch.PatchError):
        patch.patched_manager(changed)


def test_main_patches_the_file_in_place(tmp_path):
    manager = tmp_path / "agent" / "memory_manager.py"
    manager.parent.mkdir(parents=True)
    manager.write_text(MANAGER)
    assert patch.main(["hindsight_prefetch_timeout.py", str(tmp_path)]) == 0
    assert timeout(manager.read_text()) == 15.0
