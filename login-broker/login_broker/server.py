"""MCP streamable-HTTP server exposing browser_login. This container is the
ONLY place secrets flow — from VaultClient into perform_login. Every response
built here goes through mask_username / assert_no_secret_leak before it
leaves the process, including when perform_login raises."""
import os
import sys
import threading

from mcp.server.mcpserver import MCPServer

from login_broker.browser import perform_login
from login_broker.cdp import TabLimits, sweep_forever
from login_broker.redact import assert_no_secret_leak, mask_username
from login_broker.totp import generate_code
from login_broker.vault import VaultClient, VaultError

# Bind on all interfaces so hermes can reach the tool over the docker network
# at http://login-broker:8000/mcp (streamable HTTP, not SSE — issue #20349).
mcp = MCPServer("login-broker")

_vault_client = VaultClient(
    server_url=os.environ.get("VAULTWARDEN_URL", "https://vaultwarden:80"),
    master_password=os.environ.get("BW_MASTER_PASSWORD", ""),
    email=os.environ.get("VAULTWARDEN_ACCOUNT_EMAIL", "ai-browser@vault.local"),
)
_CDP_URL = os.environ.get("LOGIN_BROKER_CDP_URL", "http://172.28.0.10:9222")


@mcp.tool()
async def browser_login(url: str) -> dict:
    """Log in to a whitelisted site using vault-stored credentials. Never
    pass a password/code as an argument — this tool looks them up itself."""
    # A bare host ("example.com") parses to an empty netloc and matches
    # nothing; default it to https so both the vault lookup and the browser
    # navigation get a real origin.
    if "://" not in url:
        url = "https://" + url
    # Both calls shell out to `bw`; a VaultError from either carries an
    # internal message that would otherwise be delivered straight into the
    # chat. The cause goes to this container's log, the agent gets a reason.
    try:
        _vault_client.ensure_unlocked()
        item = _vault_client.find_item_for_url(url)
    except VaultError:
        return {"ok": False, "reason": "vault unavailable"}
    if item is None:
        return {"ok": False, "reason": "no matching vault entry"}

    totp_code = generate_code(item.totp_seed) if item.totp_seed else None
    secrets = [item.password]
    if item.totp_seed:
        secrets.append(item.totp_seed)
    if totp_code:
        secrets.append(totp_code)

    try:
        result = await perform_login(
            cdp_url=_CDP_URL, url=url,
            username=item.username, password=item.password, totp_code=totp_code,
        )
    except Exception as exc:
        # A raw exception may embed a typed value or selector data — never let
        # its *message* reach the response or a log line. Fail generically, but
        # record the class name on this container's stderr: the bare "login
        # failed" reason is indistinguishable from rejected credentials, and a
        # silent swallow once sent an investigation after a mobile-number
        # format that was correct all along while the real fault was a CDP
        # connect timeout on the shared browser.
        print(f"perform_login raised {type(exc).__name__}",
              file=sys.stderr, flush=True)
        result = {"ok": False, "reason": "login failed"}

    response = {"ok": result.get("ok", False), "username": mask_username(item.username)}
    if not result.get("ok"):
        response["reason"] = result.get("reason", "login failed")

    assert_no_secret_leak(response, secrets)
    return response


def _start_tab_sweeper() -> threading.Thread | None:
    """Sweep wedged tabs out of the shared browser in the background, not only
    before a login: hermes drives the same browser with browser_navigate, and
    one tab that wedged there failed every navigate for days. A tab is closed
    after two failed probes in a row, so ~3-6 min at the default interval.
    A tab that still answers is closed after 30 min while the browser burns
    over half a core, and after 6 h in any case (cdp.close_old_targets).
    LOGIN_BROKER_TAB_SWEEP_INTERVAL_S=0 turns the sweep off; a zero
    LOGIN_BROKER_TAB_HOT_AGE_S, LOGIN_BROKER_TAB_MAX_AGE_S or
    LOGIN_BROKER_CPU_LIMIT turns that one rule off."""
    interval = float(os.environ.get("LOGIN_BROKER_TAB_SWEEP_INTERVAL_S", "180"))
    if interval <= 0:
        return None
    limits = TabLimits(
        hot_age_s=float(os.environ.get("LOGIN_BROKER_TAB_HOT_AGE_S", "1800")),
        max_age_s=float(os.environ.get("LOGIN_BROKER_TAB_MAX_AGE_S", "21600")),
        cpu_limit=float(os.environ.get("LOGIN_BROKER_CPU_LIMIT", "0.5")),
    )
    thread = threading.Thread(
        target=sweep_forever, args=(_CDP_URL, interval, threading.Event()),
        kwargs={"limits": limits}, name="tab-sweeper", daemon=True)
    thread.start()
    return thread


def main() -> None:
    _start_tab_sweeper()
    # Streamable HTTP so hermes connects as a remote MCP server. Do NOT unlock
    # eagerly: the vault may not be bootstrapped yet, and a failed unlock here
    # would take down the server. Unlock lazily on the first browser_login.
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
