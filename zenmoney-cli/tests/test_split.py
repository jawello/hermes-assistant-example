from decimal import Decimal

import pytest

from zenmoney_cli import ops
from zenmoney_cli.state import State


@pytest.fixture
def st(tmp_path, full_tx):
    st = State.load(tmp_path)
    st.apply({"serverTimestamp": 1,
              "tag": [{"id": "food", "title": "Еда", "parent": None},
                      {"id": "house", "title": "Хозтовары", "parent": None},
                      {"id": "drinks", "title": "Вода и напитки",
                       "parent": None}],
              "transaction": [full_tx]})
    return st


PARTS = [("Еда", Decimal("80.21")),
         ("Вода и напитки", Decimal("27.73")),
         ("Хозтовары", Decimal("30.74"))]


def test_split_sends_one_diff_with_modified_plus_new(st, client, fake_zen):
    fake_zen.responses.append({"serverTimestamp": 2})
    result = ops.split(client, st, "t1", PARTS)
    assert len(fake_zen.requests) == 1
    sent = fake_zen.requests[0]["transaction"]
    assert len(sent) == 3
    assert "deletion" not in fake_zen.requests[0]
    original = sent[0]
    assert original["id"] == "t1"
    assert original["outcome"] == 80.21
    assert original["tag"] == ["food"]
    new = sent[1:]
    assert {t["outcome"] for t in new} == {27.73, 30.74}
    for t in new:
        assert t["id"] != "t1"
        assert t["date"] == "2026-07-19"
        assert t["outcomeAccount"] == "acc"
        assert t["payee"] == "Supermarket"
        assert t["income"] == 0
        assert t["deleted"] is False
    assert result["ok"] is True
    assert len(result["parts"]) == 3


def test_split_rejects_sum_mismatch(st, client):
    with pytest.raises(ValueError, match="138.68"):
        ops.split(client, st, "t1", [("Еда", Decimal("100")),
                                     ("Хозтовары", Decimal("38.00"))])


def test_split_rejects_single_part_and_income_tx(st, client):
    with pytest.raises(ValueError):
        ops.split(client, st, "t1", [("Еда", Decimal("138.68"))])
    st.entities["transaction"]["t1"]["income"] = 5
    with pytest.raises(ValueError, match="income"):
        ops.split(client, st, "t1", PARTS)


def test_split_rejects_unknown_tx(st, client):
    with pytest.raises(ValueError, match="no transaction"):
        ops.split(client, st, "missing", PARTS)


def test_new_parts_carry_every_field_the_parent_has(st, client, fake_zen,
                                                    full_tx):
    # Parts must not silently drop the parent's currency and bank fields.
    ops.split(client, st, "t1", PARTS)
    for part in fake_zen.requests[0]["transaction"][1:]:
        assert set(part) == set(full_tx), \
            f"missing: {sorted(set(full_tx) - set(part))}"


def test_new_parts_do_not_inherit_bank_identity(st, client, fake_zen):
    # Two transactions pointing at one bank operation would confuse the sync
    # plugin; a split part is our own record, not the bank's.
    ops.split(client, st, "t1", PARTS)
    sent = fake_zen.requests[0]["transaction"]
    assert sent[0]["outcomeBankID"] == "[Examplebank][tmp]-deadbeef"
    for part in sent[1:]:
        assert part["outcomeBankID"] is None
        assert part["incomeBankID"] is None
        assert part["source"] is None
        assert part["hold"] is False


def test_original_currency_amount_is_split_proportionally(st, client,
                                                          fake_zen):
    # 138.68 in the account's currency was charged abroad as 100.00
    st.entities["transaction"]["t1"].update(
        opOutcome=100.0, opOutcomeInstrument=2)
    ops.split(client, st, "t1", PARTS)
    sent = fake_zen.requests[0]["transaction"]
    # Rounding must not invent or lose money in the original currency either.
    assert sum(Decimal(str(t["opOutcome"])) for t in sent) == Decimal("100.00")
    by_outcome = {t["outcome"]: t["opOutcome"] for t in sent}
    assert by_outcome[27.73] == pytest.approx(20.0, abs=0.01)


def test_original_currency_untouched_when_same_currency(st, client, fake_zen):
    # opOutcome is meaningless without opOutcomeInstrument — leave it alone.
    ops.split(client, st, "t1", PARTS)
    for part in fake_zen.requests[0]["transaction"]:
        assert part["opOutcome"] == 0
        assert part["opOutcomeInstrument"] is None
