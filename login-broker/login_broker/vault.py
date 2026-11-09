"""Bitwarden CLI wrapper. BW_SESSION lives only in this process's memory —
never written to disk, never logged. The master password is passed to `bw`
via --passwordenv (an env var), never on argv (which would land in `ps`)."""
import json
import os
import subprocess
import sys
from dataclasses import dataclass

from login_broker.matching import hosts_match


class VaultError(Exception):
    pass


def _log_bw_failure(what: str, result: subprocess.CompletedProcess) -> None:
    """Record why a `bw` call failed, on the broker's own stderr only.

    bw answers a session it rejects with an interactive master-password
    prompt rather than an error, so a failure otherwise shows up as nothing
    but a non-zero exit code. Truncated because bw inlines whole (encrypted)
    ciphers into its error text, and kept out of every VaultError message —
    those reach the agent, this does not."""
    detail = " ".join(result.stderr.split())[:200]
    print(f"bw {what} failed (rc={result.returncode}): {detail}", file=sys.stderr, flush=True)


@dataclass
class VaultItem:
    username: str
    password: str
    totp_seed: str | None


class VaultClient:
    def __init__(self, server_url: str, master_password: str, email: str):
        self._server_url = server_url
        self._master_password = master_password
        self._email = email
        self._session: str | None = None

    def _run(self, args: list[str], *, with_password: bool = False) -> subprocess.CompletedProcess:
        env = {**os.environ}
        if with_password:
            env["BW_PASSWORD"] = self._master_password
        # stdin=DEVNULL: bw prompts interactively for the master password
        # whenever it rejects a session. An inherited stdin can leave that
        # prompt waiting forever; DEVNULL turns it into an immediate EOF and
        # a non-zero exit we can act on.
        return subprocess.run(
            ["bw", *args],
            capture_output=True,
            text=True,
            env=env,
            stdin=subprocess.DEVNULL,
        )

    def _status(self) -> str:
        """bw status -> unauthenticated | locked | unlocked."""
        result = self._run(["status"])
        if result.returncode != 0:
            return "unauthenticated"
        try:
            return json.loads(result.stdout).get("status", "unauthenticated")
        except (ValueError, TypeError):
            return "unauthenticated"

    def unlock(self) -> None:
        """Obtain a vault session, authenticating as needed. `bw unlock`
        requires being logged in first, so branch on the current state:
        unauthenticated -> `bw login`, otherwise -> `bw unlock`."""
        if self._status() == "unauthenticated":
            cfg = self._run(["config", "server", self._server_url])
            if cfg.returncode != 0:
                raise VaultError("vault server config failed")
            result = self._run(
                ["login", self._email, "--passwordenv", "BW_PASSWORD", "--raw"],
                with_password=True,
            )
            # CLI 2026.9.1 authenticates, then exits 1 when its post-login
            # key-id backfill hits an endpoint Vaultwarden lacks (404). It is
            # logged in by then, so fall through to `bw unlock`.
            if result.returncode != 0 and self._status() != "unauthenticated":
                _log_bw_failure("login", result)
                result = self._run(
                    ["unlock", "--passwordenv", "BW_PASSWORD", "--raw"],
                    with_password=True,
                )
        else:
            result = self._run(
                ["unlock", "--passwordenv", "BW_PASSWORD", "--raw"],
                with_password=True,
            )
        if result.returncode != 0:
            _log_bw_failure("unlock", result)
            raise VaultError("vault unlock failed")
        self._session = result.stdout.strip()

    def ensure_unlocked(self) -> None:
        """Unlock once, lazily. Lets the MCP server start before the vault is
        reachable/bootstrapped; the first browser_login triggers the unlock."""
        if self._session is None:
            self.unlock()

    def find_item_for_url(self, url: str) -> VaultItem | None:
        if not self._session:
            raise VaultError("call unlock() first")
        result = self._run(["list", "items", "--session", self._session])
        if result.returncode != 0:
            # `bw unlock` invalidates every session key it issued before, so a
            # session cached for the life of this process can go stale under
            # us. Re-unlock once and retry: without this, one stale session
            # fails every later browser_login until the container restarts.
            _log_bw_failure("list items", result)
            self.unlock()
            result = self._run(["list", "items", "--session", self._session])
            if result.returncode != 0:
                _log_bw_failure("list items (after re-unlock)", result)
                raise VaultError("vault list failed")
        for raw in json.loads(result.stdout):
            login = raw.get("login") or {}
            uris = login.get("uris") or []
            if any(hosts_match(url, u.get("uri", "")) for u in uris):
                return VaultItem(
                    username=login.get("username", ""),
                    password=login.get("password", ""),
                    totp_seed=login.get("totp"),
                )
        return None
