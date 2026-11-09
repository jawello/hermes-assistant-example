"""Thin client for the Zen-Money v8 diff API."""
import time

import httpx

BODY_LIMIT = 1000


class ZenMoneyError(Exception):
    """An API call failed, carrying whatever the server said about it."""


class ZenMoneyClient:
    def __init__(self, token: str, base_url: str = "https://api.zenmoney.ru",
                 transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(
            base_url=base_url,
            transport=transport,
            timeout=30,
            headers={"Authorization": f"Bearer {token}"},
        )

    def diff(self, server_timestamp: int,
             force_fetch: list[str] | None = None, **entities) -> dict:
        body: dict = {
            "currentClientTimestamp": int(time.time()),
            "serverTimestamp": server_timestamp,
        }
        if force_fetch:
            body["forceFetch"] = force_fetch
        body.update(entities)
        resp = self._http.post("/v8/diff/", json=body)
        if resp.is_error:
            # raise_for_status() would drop the body, and the body is the only
            # place the server explains *which* field it disliked.
            detail = resp.text.strip()[:BODY_LIMIT] or "<empty body>"
            raise ZenMoneyError(f"Zen-Money API {resp.status_code}: {detail}")
        return resp.json()
