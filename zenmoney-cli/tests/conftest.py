import json

import httpx
import pytest

from zenmoney_cli.api import ZenMoneyClient


class FakeZen:
    """Records diff requests, returns queued responses."""

    def __init__(self):
        self.requests: list[dict] = []
        self.responses: list[dict] = []

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v8/diff/"
            assert request.headers["Authorization"] == "Bearer tok"
            self.requests.append(json.loads(request.content))
            body = self.responses.pop(0) if self.responses else {
                "serverTimestamp": 1}
            return httpx.Response(200, json=body)
        return httpx.MockTransport(handler)


# The full field set the server actually stores on a transaction. Sending
# anything short of it gets the whole diff rejected with a bare 400.
FULL_TX = {
    "id": "t1", "user": 42, "date": "2026-07-19",
    "created": 1_750_000_000, "changed": 1_750_000_000,
    "deleted": False, "hold": False, "viewed": True,
    "income": 0, "incomeAccount": "acc", "incomeInstrument": 3,
    "outcome": 138.68, "outcomeAccount": "acc", "outcomeInstrument": 3,
    "opIncome": 0, "opIncomeInstrument": None,
    "opOutcome": 0, "opOutcomeInstrument": None,
    "incomeBankID": None, "outcomeBankID": "[Examplebank][tmp]-deadbeef",
    "payee": "Supermarket", "originalPayee": "Supermarket",
    "merchant": None, "comment": None, "tag": None,
    "qrCode": None, "reminderMarker": None, "source": "plugin",
    "latitude": None, "longitude": None,
}


@pytest.fixture
def full_tx():
    return dict(FULL_TX)


@pytest.fixture
def fake_zen():
    return FakeZen()


@pytest.fixture
def client(fake_zen):
    return ZenMoneyClient("tok", transport=fake_zen.transport())
