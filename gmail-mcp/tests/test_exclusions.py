import pytest

from gmail_mcp.exclusions import (
    SKIP_LABEL,
    Exclusions,
    address_of,
    excluded_reason,
    load_exclusions,
)
from gmail_mcp.gmail import MessageMeta


def meta(sender="a@b.example", labels=None):
    return MessageMeta(
        id="m1",
        thread_id="t1",
        sender=sender,
        subject="s",
        date="d",
        list_id="",
        label_ids=list(labels or ["INBOX"]),
    )


RULES = Exclusions(senders=["payslip@work.example"], domains=["clinic.example"])
ARGS = {"skip_label_id": "Label_7", "self_address": "owner@example.com"}


def test_nothing_matches_returns_none():
    assert excluded_reason(meta(), RULES, **ARGS) is None


def test_the_skip_label_excludes():
    assert excluded_reason(meta(labels=["INBOX", "Label_7"]), RULES, **ARGS) == "skip label"


def test_a_denied_sender_excludes():
    assert excluded_reason(meta("Payroll <payslip@work.example>"), RULES, **ARGS) == "denied sender"


def test_a_denied_domain_excludes_any_address_in_it():
    assert excluded_reason(meta("doc@clinic.example"), RULES, **ARGS) == "denied domain"


def test_a_denied_domain_does_not_match_a_lookalike():
    # The classic suffix bug: "clinic.example" must not match
    # "notclinic.example" or "clinic.example.attacker.test".
    assert excluded_reason(meta("x@notclinic.example"), RULES, **ARGS) is None
    assert excluded_reason(meta("x@clinic.example.attacker.test"), RULES, **ARGS) is None


def test_a_subdomain_of_a_denied_domain_is_excluded():
    assert excluded_reason(meta("x@mail.clinic.example"), RULES, **ARGS) == "denied domain"


def test_matching_ignores_case():
    assert excluded_reason(meta("PaySlip@Work.Example"), RULES, **ARGS) == "denied sender"


def test_the_assistants_own_mail_is_excluded():
    # Otherwise a message the assistant sent gets ingested, remembered, and
    # can propose a task about itself.
    assert excluded_reason(meta("the user <owner@example.com>"), RULES, **ARGS) == "self"


def test_a_missing_skip_label_id_never_excludes_everything():
    # label_id() returns None when the label does not exist yet. Passing that
    # through must not make `None in label_ids` accidentally true.
    assert excluded_reason(meta(), RULES, skip_label_id=None, self_address="owner@example.com") is None


@pytest.mark.parametrize(
    "header,expected",
    [
        ("Bank <no-reply@bank.example>", "no-reply@bank.example"),
        ("no-reply@bank.example", "no-reply@bank.example"),
        ('"Doe, John" <j@d.example>', "j@d.example"),
        ("", ""),
    ],
)
def test_address_of_pulls_the_bare_address(header, expected):
    assert address_of(header) == expected


def test_load_exclusions_reads_the_shipped_file(tmp_path):
    path = tmp_path / "exclusions.yaml"
    path.write_text("senders:\n  - a@b.example\ndomains:\n  - c.example\n", encoding="utf-8")
    rules = load_exclusions(path)
    assert rules.senders == ["a@b.example"]
    assert rules.domains == ["c.example"]


def test_load_exclusions_accepts_an_empty_file(tmp_path):
    path = tmp_path / "exclusions.yaml"
    path.write_text("", encoding="utf-8")
    rules = load_exclusions(path)
    assert rules.senders == []
    assert rules.domains == []


def test_skip_label_name_is_the_one_the_user_applies():
    assert SKIP_LABEL == "assistant/skip"
