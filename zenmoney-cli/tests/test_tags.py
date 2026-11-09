import pytest

from zenmoney_cli import ops
from zenmoney_cli.state import State


@pytest.fixture
def st(tmp_path):
    st = State.load(tmp_path)
    st.apply({"serverTimestamp": 1, "tag": [
        {"id": "misc", "title": "Разное", "parent": None},
        {"id": "cats", "title": "Хобби", "parent": "misc"},
        {"id": "food", "title": "Еда", "parent": None},
        {"id": "food2", "title": "Еда", "parent": "misc"},
    ]})
    return st


def test_tag_path_walks_parents(st):
    assert ops.tag_path(st, "cats") == "Разное → Хобби"
    assert ops.tag_path(st, "food") == "Еда"


def test_list_tags_sorted_with_paths(st):
    paths = [t["path"] for t in ops.list_tags(st)]
    assert "Разное → Хобби" in paths
    assert paths == sorted(paths)


def test_resolve_unique_title_case_insensitive(st):
    assert ops.resolve_tag(st, "Хобби") == "cats"


def test_resolve_full_path_with_slash_separator(st):
    assert ops.resolve_tag(st, "разное / еда") == "food2"


def test_resolve_ambiguous_title_raises_with_candidates(st):
    with pytest.raises(ValueError, match="Разное → Еда"):
        ops.resolve_tag(st, "Еда")


def test_resolve_unknown_raises_with_suggestions(st):
    with pytest.raises(ValueError, match="Хобби"):
        ops.resolve_tag(st, "хобб")
