import os

from zenmoney_cli.state import State


def test_load_missing_starts_empty(tmp_path):
    st = State.load(tmp_path)
    assert st.server_timestamp == 0
    assert st.entities["transaction"] == {}


def test_apply_upserts_and_save_roundtrips(tmp_path):
    st = State.load(tmp_path)
    st.apply({
        "serverTimestamp": 100,
        "transaction": [{"id": "t1", "outcome": 5.0}],
        "tag": [{"id": "tag1", "title": "Еда", "parent": None}],
    })
    st.save()
    again = State.load(tmp_path)
    assert again.server_timestamp == 100
    assert again.entities["transaction"]["t1"]["outcome"] == 5.0
    assert again.entities["tag"]["tag1"]["title"] == "Еда"


def test_apply_deletion_removes_from_cache(tmp_path):
    st = State.load(tmp_path)
    st.apply({"serverTimestamp": 1, "transaction": [{"id": "t1"}]})
    st.apply({"serverTimestamp": 2,
              "deletion": [{"id": "t1", "object": "transaction"}]})
    assert "t1" not in st.entities["transaction"]


def test_save_is_0600(tmp_path):
    st = State.load(tmp_path)
    st.save()
    mode = os.stat(tmp_path / "state.json").st_mode & 0o777
    assert mode == 0o600


def test_apply_ignores_unknown_entities(tmp_path):
    st = State.load(tmp_path)
    st.apply({"serverTimestamp": 5, "budget": [{"id": "b1"}]})
    assert "budget" not in st.entities
