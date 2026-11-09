from unittest.mock import AsyncMock, patch
import pytest
import login_broker.server as srv
from login_broker import cdp
from login_broker.vault import VaultItem, VaultError
from login_broker.server import browser_login


def test_main_serves_streamable_http_on_all_interfaces():
    # mcp 2 takes host/port at run(); 0.0.0.0 so hermes reaches it by service
    # name (DNS-rebinding protection only auto-enables for localhost).
    with patch.object(srv.mcp, "run") as run, patch.object(srv, "_start_tab_sweeper"):
        srv.main()
    run.assert_called_once_with(transport="streamable-http", host="0.0.0.0", port=8000)


def test_main_starts_the_periodic_tab_sweep_before_serving():
    order = []
    with patch.object(srv.mcp, "run", side_effect=lambda **kw: order.append("run")), \
         patch.object(srv, "_start_tab_sweeper", side_effect=lambda: order.append("sweep")):
        srv.main()
    assert order == ["sweep", "run"]


def test_tab_sweeper_runs_every_three_minutes_by_default(monkeypatch):
    monkeypatch.delenv("LOGIN_BROKER_TAB_SWEEP_INTERVAL_S", raising=False)
    with patch.object(srv, "sweep_forever") as sweep:
        thread = srv._start_tab_sweeper()
        thread.join(timeout=2)
    assert thread.daemon
    url, interval, _stop = sweep.call_args.args
    assert (url, interval) == (srv._CDP_URL, 180.0)


def test_tab_sweeper_interval_zero_turns_it_off(monkeypatch):
    monkeypatch.setenv("LOGIN_BROKER_TAB_SWEEP_INTERVAL_S", "0")
    with patch.object(srv, "sweep_forever") as sweep:
        assert srv._start_tab_sweeper() is None
    sweep.assert_not_called()


@pytest.mark.asyncio
async def test_browser_login_swallows_exceptions_without_leaking_secret():
    item = VaultItem(username="99000001", password="s3cr3t-pw", totp_seed=None)
    with patch.object(srv._vault_client, "ensure_unlocked"), \
         patch.object(srv._vault_client, "find_item_for_url", return_value=item), \
         patch("login_broker.server.perform_login",
               AsyncMock(side_effect=RuntimeError("boom s3cr3t-pw"))):
        response = await srv.browser_login("https://example.com")
    assert response == {"ok": False, "username": "99******", "reason": "login failed"}
    assert "s3cr3t-pw" not in str(response)


@pytest.mark.asyncio
async def test_browser_login_prepends_https_to_schemeless_url():
    # A bare host (no scheme) parses to an empty netloc and never matches;
    # browser_login normalizes it to https:// before looking it up.
    with patch("login_broker.server._vault_client") as vault:
        vault.find_item_for_url.return_value = None
        await browser_login("example-shop.com")
    vault.find_item_for_url.assert_called_once_with("https://example-shop.com")


@pytest.mark.asyncio
async def test_browser_login_no_vault_item_returns_clean_error():
    with patch("login_broker.server._vault_client") as vault:
        vault.find_item_for_url.return_value = None
        result = await browser_login("https://unknown-site.com")
    assert result == {"ok": False, "reason": "no matching vault entry"}


@pytest.mark.asyncio
async def test_browser_login_reports_a_clean_reason_when_the_vault_lookup_fails():
    # find_item_for_url was outside the VaultError guard, so its internal
    # message ("vault list failed") was delivered verbatim into the chat.
    with patch("login_broker.server._vault_client") as vault:
        vault.find_item_for_url.side_effect = VaultError("vault list failed")
        result = await browser_login("https://example.com")
    assert result == {"ok": False, "reason": "vault unavailable"}


@pytest.mark.asyncio
async def test_browser_login_success_response_has_no_secrets():
    item = VaultItem(username="the user", password="s3cr3t-pw", totp_seed=None)
    with patch("login_broker.server._vault_client") as vault, \
         patch("login_broker.server.perform_login", new=AsyncMock(return_value={"ok": True})):
        vault.find_item_for_url.return_value = item
        result = await browser_login("https://example.com/login")
    assert result["ok"] is True
    assert result["username"] == "th******"
    assert "s3cr3t-pw" not in str(result)


@pytest.mark.asyncio
async def test_browser_login_with_totp_generates_code_and_does_not_leak_it():
    item = VaultItem(username="the user", password="s3cr3t-pw", totp_seed="JBSWY3DPEHPK3PXP")
    with patch("login_broker.server._vault_client") as vault, \
         patch("login_broker.server.perform_login", new=AsyncMock(return_value={"ok": True})) as pl:
        vault.find_item_for_url.return_value = item
        result = await browser_login("https://example.com/login")
    assert "JBSWY3DPEHPK3PXP" not in str(result)
    # the generated 6-digit code must have been passed to perform_login,
    # but must not appear in the tool's return value
    passed_code = pl.call_args.kwargs["totp_code"]
    assert passed_code not in str(result)


@pytest.mark.asyncio
async def test_browser_login_logs_the_exception_class_it_swallows(capsys):
    # The generic "login failed" reason is indistinguishable from rejected
    # credentials, which sent a real investigation after the wrong cause (a
    # mobile-number format that was in fact correct) while the actual failure
    # was a CDP connect timeout. The response stays generic; the container log
    # must name the exception class so the next failure is diagnosable. Only
    # the class name — an exception *message* can embed a typed value.
    item = VaultItem(username="99000001", password="s3cr3t-pw", totp_seed=None)
    with patch.object(srv._vault_client, "ensure_unlocked"), \
         patch.object(srv._vault_client, "find_item_for_url", return_value=item), \
         patch("login_broker.server.perform_login",
               AsyncMock(side_effect=TimeoutError("connect_over_cdp s3cr3t-pw"))):
        response = await srv.browser_login("https://example.com")
    assert response == {"ok": False, "username": "99******", "reason": "login failed"}
    logged = capsys.readouterr().err
    assert "TimeoutError" in logged
    assert "s3cr3t-pw" not in logged
    assert "connect_over_cdp" not in logged


def test_tab_sweeper_closes_old_tabs_at_30_minutes_hot_and_6_hours(monkeypatch):
    for name in ("LOGIN_BROKER_TAB_HOT_AGE_S", "LOGIN_BROKER_TAB_MAX_AGE_S",
                 "LOGIN_BROKER_CPU_LIMIT"):
        monkeypatch.delenv(name, raising=False)
    with patch.object(srv, "sweep_forever") as sweep:
        srv._start_tab_sweeper().join(timeout=2)
    assert sweep.call_args.kwargs["limits"] == cdp.TabLimits(
        hot_age_s=1800, max_age_s=21600, cpu_limit=0.5)
