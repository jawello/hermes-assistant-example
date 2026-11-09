"""Google OAuth credentials for the Gmail services.

The refresh token arrives through the environment from secrets.sops.yaml —
nothing is written to disk here, and hermes never receives these variables.
The interactive flow that produces the token runs on a laptop, not here:
Google blocked the out-of-band redirect in 2022 and its device flow does not
support Gmail scopes, so a browser has to be reachable from wherever the flow
runs. See `login()` at the bottom.
"""
import os
from typing import Mapping

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

TOKEN_URI = "https://oauth2.googleapis.com/token"

# What the consent screen asks for. readonly: read mail and label ids.
# compose: create drafts and send them. modify: archive and move to Trash
# (2026-09-25). Never https://mail.google.com/ — the only scope that can
# delete mail permanently.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",
]

_REQUIRED = ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN")


class AuthError(Exception):
    """Credentials are missing, or a refresh failed.

    Messages name variables, never values.
    """


def load_credentials(env: Mapping[str, str] | None = None) -> Credentials:
    """Build refreshable credentials from the environment."""
    env = os.environ if env is None else env
    # Empty counts as missing: compose interpolates an unset variable to "",
    # so a forgotten sops entry arrives as an empty string, not as absent.
    missing = [name for name in _REQUIRED if not env.get(name)]
    if missing:
        raise AuthError(f"missing or empty: {', '.join(missing)}")
    return Credentials(
        token=None,
        refresh_token=env["GMAIL_REFRESH_TOKEN"],
        client_id=env["GMAIL_CLIENT_ID"],
        client_secret=env["GMAIL_CLIENT_SECRET"],
        token_uri=TOKEN_URI,
        # No scopes on refresh: Google rejects one that names a scope the
        # token was not granted, so SCOPES growing would take the mailbox
        # offline until the new token arrived. The access token gets whatever
        # the refresh token carries instead.
    )


def refresh(creds, request=None) -> None:
    """Refresh the access token in place.

    The original exception is deliberately dropped from the message: google-auth
    embeds the token endpoint's response body, which contains token material,
    and this message ends up in the poller log.
    """
    try:
        creds.refresh(request if request is not None else Request())
    except Exception as exc:  # noqa: BLE001 — the type is all we may keep
        raise AuthError(f"token refresh failed ({type(exc).__name__})") from None


def login() -> None:
    """Run the interactive consent flow and print the refresh token's length.

    Run this on a machine with a browser, never on the VPS:

        GMAIL_CLIENT_ID=... GMAIL_CLIENT_SECRET=... python -m gmail_mcp.auth

    It opens a browser, and on success writes `gmail-refresh-token.txt` in the
    working directory. Put that value into secrets.sops.yaml as
    GMAIL_REFRESH_TOKEN and delete the file.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    client_id = os.environ.get("GMAIL_CLIENT_ID", "")
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise AuthError("missing or empty: GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET")

    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": TOKEN_URI,
            }
        },
        scopes=SCOPES,
    )
    creds = flow.run_local_server(port=0, prompt="consent")
    if not creds.refresh_token:
        raise AuthError(
            "Google returned no refresh token. Revoke the app's access at "
            "https://myaccount.google.com/permissions and run this again."
        )
    # The newline is deliberate: without it zsh renders its own inverse "%"
    # end-of-line marker after `cat`, and that character gets copied along
    # with the token. Google then answers invalid_grant on every refresh.
    with open("gmail-refresh-token.txt", "w", encoding="utf-8") as handle:
        handle.write(creds.refresh_token + "\n")
    # Never print the value itself — this terminal has scrollback.
    print(f"Wrote gmail-refresh-token.txt ({len(creds.refresh_token)} chars).")
    print("Add it to secrets.sops.yaml as GMAIL_REFRESH_TOKEN, then delete the file.")


if __name__ == "__main__":
    login()
