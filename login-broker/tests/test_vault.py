from pathlib import Path
from unittest.mock import patch, MagicMock
import json
import re
import subprocess
import pytest
from login_broker.vault import VaultClient, VaultItem, VaultError

FAKE_ITEM = {
    "login": {
        "username": "the user",
        "password": "s3cr3t-pw",
        "uris": [{"uri": "https://example.com"}],
        "totp": "BASE32SEEDXXXX",
    }
}


def _client():
    return VaultClient(
        server_url="https://vaultwarden",
        master_password="pw",
        email="ai-browser@vault.local",
    )


def test_unlock_logs_in_when_unauthenticated():
    # status -> unauthenticated, config server -> ok, login -> session.
    with patch("login_broker.vault.subprocess.run") as run:
        run.side_effect = [
            MagicMock(returncode=0, stdout='{"status":"unauthenticated"}', stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="fake-session-token\n", stderr=""),
        ]
        client = _client()
        client.unlock()
        assert client._session == "fake-session-token"
        # last call is `bw login <email> --passwordenv BW_PASSWORD --raw`
        argv = run.call_args.args[0]
        assert argv[:2] == ["bw", "login"]
        assert "--passwordenv" in argv and "BW_PASSWORD" in argv
        # the master password is never on argv (would land in `ps`)
        assert "pw" not in " ".join(argv)


def test_unlock_uses_unlock_when_already_authenticated():
    # status -> locked, so we go straight to `bw unlock` (no login/config).
    with patch("login_broker.vault.subprocess.run") as run:
        run.side_effect = [
            MagicMock(returncode=0, stdout='{"status":"locked"}', stderr=""),
            MagicMock(returncode=0, stdout="unlock-session\n", stderr=""),
        ]
        client = _client()
        client.unlock()
        assert client._session == "unlock-session"
        argv = run.call_args.args[0]
        assert argv[:2] == ["bw", "unlock"]
        assert "--passwordenv" in argv


def test_unlock_raises_on_bw_failure_without_leaking_password():
    with patch("login_broker.vault.subprocess.run") as run:
        run.side_effect = [
            MagicMock(returncode=0, stdout='{"status":"unauthenticated"}', stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=1, stdout="", stderr="Invalid master password."),
            # A rejected login leaves the CLI logged out.
            MagicMock(returncode=0, stdout='{"status":"unauthenticated"}', stderr=""),
        ]
        client = _client()
        with pytest.raises(VaultError) as exc:
            client.unlock()
        assert "pw" not in str(exc.value)


def test_unlock_recovers_when_login_fails_after_authenticating():
    # CLI 2026.9.1 against Vaultwarden 1.37.3: `bw login` authenticates, then
    # its post-login key-id backfill hits an endpoint Vaultwarden lacks (404)
    # and exits 1. The CLI is logged in (status "locked"), so `bw unlock`
    # gets the session. Without this every browser_login right after a
    # broker restart failed (seen 2026-10-05).
    with patch("login_broker.vault.subprocess.run") as run:
        run.side_effect = [
            MagicMock(returncode=0, stdout='{"status":"unauthenticated"}', stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=1, stdout="", stderr="KeyIdBackfillError: 404"),
            MagicMock(returncode=0, stdout='{"status":"locked"}', stderr=""),
            MagicMock(returncode=0, stdout="unlock-session\n", stderr=""),
        ]
        client = _client()
        client.unlock()
        assert client._session == "unlock-session"
        assert run.call_args.args[0][:2] == ["bw", "unlock"]


def test_ensure_unlocked_only_unlocks_once():
    client = _client()
    with patch.object(client, "unlock") as unlock:
        client.ensure_unlocked()
        client._session = "cached"  # simulate unlock having set it
        client.ensure_unlocked()
        unlock.assert_called_once()


def test_find_item_for_url_returns_matching_item():
    with patch("login_broker.vault.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout=json.dumps([FAKE_ITEM]), stderr="")
        client = _client()
        client._session = "fake-session-token"
        item = client.find_item_for_url("https://example.com/login")
        assert isinstance(item, VaultItem)
        assert item.username == "the user"
        assert item.password == "s3cr3t-pw"
        assert item.totp_seed == "BASE32SEEDXXXX"


def test_find_item_for_url_rejects_subdomain():
    with patch("login_broker.vault.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout=json.dumps([FAKE_ITEM]), stderr="")
        client = _client()
        client._session = "fake-session-token"
        assert client.find_item_for_url("https://sub.example.com/login") is None


def test_bw_never_inherits_stdin():
    # bw answers a rejected session with an interactive master-password prompt.
    # With an inherited stdin that prompt can block the broker forever; with
    # DEVNULL it gets EOF and bw exits non-zero, which we can handle.
    with patch("login_broker.vault.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout='{"status":"locked"}', stderr="")
        _client()._status()
        assert run.call_args.kwargs["stdin"] is subprocess.DEVNULL


def test_find_item_for_url_reunlocks_once_when_the_session_went_stale():
    # `bw unlock` invalidates every previously issued session key, so the
    # cached session can die under a long-running broker.
    with patch("login_broker.vault.subprocess.run") as run:
        run.side_effect = [
            MagicMock(returncode=1, stdout="", stderr="? Master password:"),
            MagicMock(returncode=0, stdout='{"status":"locked"}', stderr=""),
            MagicMock(returncode=0, stdout="fresh-session\n", stderr=""),
            MagicMock(returncode=0, stdout=json.dumps([FAKE_ITEM]), stderr=""),
        ]
        client = _client()
        client._session = "stale-session"
        item = client.find_item_for_url("https://example.com/login")
        assert item.username == "the user"
        assert client._session == "fresh-session"


def test_find_item_for_url_keeps_bw_stderr_out_of_the_raised_error():
    # bw dumps whole (encrypted) ciphers into its error text, and this
    # exception reaches the agent — it must stay generic.
    blob = "2.YeMKR24+eba4a3v4eeU6Ag==|TBm8Z7aAEeQ"
    with patch("login_broker.vault.subprocess.run") as run:
        run.side_effect = [
            MagicMock(returncode=1, stdout="", stderr=f"Error: invalid type {blob}"),
            MagicMock(returncode=0, stdout='{"status":"locked"}', stderr=""),
            MagicMock(returncode=0, stdout="fresh-session\n", stderr=""),
            MagicMock(returncode=1, stdout="", stderr=f"Error: invalid type {blob}"),
        ]
        client = _client()
        client._session = "stale-session"
        with pytest.raises(VaultError) as exc:
            client.find_item_for_url("https://example.com/login")
        assert str(exc.value) == "vault list failed"
        assert blob not in str(exc.value)


def test_bw_failure_is_logged_so_the_cause_is_diagnosable(capsys):
    # Without this the only trace of a broken vault is "vault list failed",
    # which is what made the 2026-08-16 outage undiagnosable from logs.
    with patch("login_broker.vault.subprocess.run") as run:
        run.side_effect = [
            MagicMock(returncode=1, stdout="", stderr="Error: invalid type, expected a string"),
            MagicMock(returncode=0, stdout='{"status":"locked"}', stderr=""),
            MagicMock(returncode=0, stdout="fresh-session\n", stderr=""),
            MagicMock(returncode=0, stdout=json.dumps([FAKE_ITEM]), stderr=""),
        ]
        client = _client()
        client._session = "stale-session"
        client.find_item_for_url("https://example.com/login")
    logged = capsys.readouterr().err
    assert "rc=1" in logged
    assert "expected a string" in logged


def test_dockerfile_pins_the_bitwarden_cli_version():
    # An unpinned `npm install -g @bitwarden/cli` let a rebuild pick up
    # 2026.7.0, whose WASM SDK crashes on `bw list items` against this
    # Vaultwarden. The version must move deliberately, not on rebuild.
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()
    install = [line for line in dockerfile.splitlines() if "@bitwarden/cli" in line]
    assert install, "Dockerfile no longer installs the Bitwarden CLI"
    assert re.search(r"@bitwarden/cli@\d{4}\.\d+\.\d+", install[0])
