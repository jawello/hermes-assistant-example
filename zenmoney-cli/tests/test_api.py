import httpx
import pytest

from zenmoney_cli.api import ZenMoneyClient, ZenMoneyError


def _client(status: int, body):
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)
    return ZenMoneyClient("tok", transport=httpx.MockTransport(handler))


def test_error_carries_server_explanation():
    # The whole point: a bare "400 Bad Request" is useless for debugging, the
    # server's own complaint must survive.
    client = _client(400, {"error": "transaction[0].opOutcome is required"})
    with pytest.raises(ZenMoneyError) as exc:
        client.diff(0)
    assert "400" in str(exc.value)
    assert "opOutcome is required" in str(exc.value)


def test_error_includes_status_for_empty_body():
    client = _client(500, "")
    with pytest.raises(ZenMoneyError, match="500"):
        client.diff(0)


def test_long_body_is_truncated():
    client = _client(400, "x" * 5000)
    with pytest.raises(ZenMoneyError) as exc:
        client.diff(0)
    assert len(str(exc.value)) < 1200


def test_success_returns_payload():
    client = _client(200, {"serverTimestamp": 7})
    assert client.diff(0) == {"serverTimestamp": 7}
