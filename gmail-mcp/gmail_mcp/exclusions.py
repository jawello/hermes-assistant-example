"""The check that decides a message must never leave the VPS.

Runs before anything is sent anywhere — not the body, not the subject, not the
sender. This is the only control the user has over what reaches a third-party
model, so it errs towards excluding.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# The label the user applies himself, in Gmail, to keep a thread out of the
# pipeline. Renaming this silently un-excludes everything he has marked.
SKIP_LABEL = "assistant/skip"

_ANGLE = re.compile(r"<([^>]+)>")


@dataclass(frozen=True)
class Exclusions:
    senders: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)


def load_exclusions(path: str | Path) -> Exclusions:
    text = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    return Exclusions(
        senders=[str(s).lower() for s in (data.get("senders") or [])],
        domains=[str(d).lower() for d in (data.get("domains") or [])],
    )


def address_of(header: str) -> str:
    """The bare address from a From header value."""
    match = _ANGLE.search(header)
    return (match.group(1) if match else header).strip().lower()


def excluded_reason(meta, rules: Exclusions, *, skip_label_id, self_address) -> str | None:
    """Why this message must not leave the VPS, or None."""
    if skip_label_id is not None and skip_label_id in meta.label_ids:
        return "skip label"

    address = address_of(meta.sender)
    if not address:
        return None

    if self_address and address == self_address.strip().lower():
        return "self"
    if address in rules.senders:
        return "denied sender"

    domain = address.rpartition("@")[2]
    for denied in rules.domains:
        # Anchored on a dot boundary: "clinic.example" covers
        # "mail.clinic.example" but not "notclinic.example", and never matches
        # a domain that merely starts with it.
        if domain == denied or domain.endswith("." + denied):
            return "denied domain"
    return None
