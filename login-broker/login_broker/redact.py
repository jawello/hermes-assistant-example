"""Guardrails against secret material leaking into any broker output."""
import json


def mask_username(username: str) -> str:
    if len(username) <= 2:
        return "*" * len(username)
    return username[:2] + "*" * (len(username) - 2)


def assert_no_secret_leak(payload: dict, secrets: list[str]) -> None:
    """Raise ValueError if any secret string appears anywhere in payload
    once serialized. Call this on every dict about to leave the process
    (MCP tool response, log line) before it is emitted."""
    serialized = json.dumps(payload)
    for secret in secrets:
        if secret and secret in serialized:
            raise ValueError("secret material detected in outbound payload")
