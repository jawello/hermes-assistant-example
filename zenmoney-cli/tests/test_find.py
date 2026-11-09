from decimal import Decimal

import pytest

from zenmoney_cli import ops
from zenmoney_cli.state import State


def tx(id, d, outcome, payee=None, income=0, deleted=False):
    return {"id": id, "date": d, "outcome": outcome, "income": income,
            "payee": payee, "originalPayee": payee, "deleted": deleted,
            "tag": None, "incomeAccount": "acc", "outcomeAccount": "acc"}


@pytest.fixture
def st(tmp_path):
    st = State.load(tmp_path)
    st.apply({"serverTimestamp": 1, "transaction": [
        tx("hit", "2026-07-19", 138.68, "Supermarket CENTRAL"),
        tx("near", "2026-07-19", 140.00, "Supermarket CENTRAL"),
        tx("far-date", "2026-07-01", 138.68, "Supermarket"),
        tx("other", "2026-07-19", 12.00, "CAFE"),
        tx("dead", "2026-07-19", 138.68, "Supermarket", deleted=True),
        tx("income", "2026-07-19", 0, "SALARY", income=500),
    ]})
    return st


def test_exact_match_first_and_flagged(st):
    got = ops.find(st, "2026-07-19", Decimal("138.68"), payee="Supermarket")
    assert got[0]["id"] == "hit"
    assert got[0]["match"] == "exact"
    assert got[0]["payee_hit"] is True


def test_near_match_flagged_near(st):
    got = ops.find(st, "2026-07-19", Decimal("138.68"))
    near = [g for g in got if g["id"] == "near"]
    assert near and near[0]["match"] == "near"


def test_window_deleted_income_excluded(st):
    ids = {g["id"] for g in ops.find(st, "2026-07-19", Decimal("138.68"))}
    assert "far-date" not in ids
    assert "dead" not in ids
    assert "income" not in ids
