from decimal import Decimal

import pytest

from zenmoney_cli import ops
from zenmoney_cli.state import State


@pytest.fixture
def st(tmp_path):
    st = State.load(tmp_path)
    st.apply({"serverTimestamp": 1,
              "user": [{"id": 42}],
              "tag": [{"id": "food", "title": "Еда", "parent": None}],
              "account": [
                  {"id": "card", "title": "Карта", "type": "ccard",
                   "instrument": 3, "deleted": False},
                  {"id": "cash", "title": "Наличные", "type": "cash",
                   "instrument": 3, "deleted": False}]})
    return st


def test_create_defaults_to_single_card(st, client, fake_zen):
    fake_zen.responses.append({"serverTimestamp": 2})
    out = ops.create(client, st, "2026-07-20", Decimal("15.00"),
                     "Bakery", "Еда")
    sent = fake_zen.requests[0]["transaction"][0]
    assert sent["outcomeAccount"] == "card"
    assert sent["outcome"] == 15.0
    assert sent["income"] == 0
    assert sent["tag"] == ["food"]
    assert sent["user"] == 42
    assert out["ok"] is True


def test_create_explicit_account_by_title(st, client, fake_zen):
    fake_zen.responses.append({"serverTimestamp": 2})
    ops.create(client, st, "2026-07-20", Decimal("5"), "x", "Еда",
               account="наличные")
    assert fake_zen.requests[0]["transaction"][0]["outcomeAccount"] == "cash"


def test_create_sends_the_full_field_set(st, client, fake_zen, full_tx):
    # Same gap as split had: the object must carry every field the server
    # stores, not a hand-picked subset.
    fake_zen.responses.append({"serverTimestamp": 2})
    ops.create(client, st, "2026-07-20", Decimal("15.00"), "Bakery", "Еда")
    sent = fake_zen.requests[0]["transaction"][0]
    assert set(sent) == set(full_tx), \
        f"missing: {sorted(set(full_tx) - set(sent))}"
    # A hand-made transaction belongs to no bank operation.
    assert sent["outcomeBankID"] is None
    assert sent["source"] is None
    assert sent["hold"] is False


def test_create_ambiguous_card_requires_account(st, client):
    st.entities["account"]["card2"] = {"id": "card2", "title": "Карта2",
                                       "type": "ccard", "instrument": 3,
                                       "deleted": False}
    with pytest.raises(ValueError, match="Карта2"):
        ops.create(client, st, "2026-07-20", Decimal("5"), "x", "Еда")
