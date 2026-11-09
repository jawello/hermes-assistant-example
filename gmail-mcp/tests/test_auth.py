import pytest

from gmail_mcp.auth import SCOPES, AuthError, load_credentials, refresh

FULL_ENV = {
    "GMAIL_CLIENT_ID": "id.apps.googleusercontent.com",
    "GMAIL_CLIENT_SECRET": "shhh",
    "GMAIL_REFRESH_TOKEN": "1//refresh",
}


def test_login_asks_for_read_compose_and_modify():
    # modify: archive and move to Trash. Never https://mail.google.com/, the
    # only scope that can delete mail permanently.
    assert set(SCOPES) == {
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.compose",
        "https://www.googleapis.com/auth/gmail.modify",
    }
    assert "https://mail.google.com/" not in SCOPES


def test_missing_variables_are_all_named_at_once():
    with pytest.raises(AuthError) as exc:
        load_credentials({"GMAIL_CLIENT_ID": "id"})
    message = str(exc.value)
    assert "GMAIL_CLIENT_SECRET" in message
    assert "GMAIL_REFRESH_TOKEN" in message


def test_empty_string_counts_as_missing():
    # compose interpolates unset variables to "", so a forgotten sops entry
    # arrives as empty rather than absent.
    with pytest.raises(AuthError):
        load_credentials({**FULL_ENV, "GMAIL_REFRESH_TOKEN": ""})


def test_error_never_repeats_a_secret_value():
    with pytest.raises(AuthError) as exc:
        load_credentials({"GMAIL_CLIENT_SECRET": "shhh"})
    assert "shhh" not in str(exc.value)


def test_credentials_do_not_request_scopes_on_refresh():
    # Google rejects a refresh that asks for a scope the token was not
    # granted (invalid_scope). Requesting none gives the access token whatever
    # the refresh token carries, so a new scope list can deploy before the
    # new token does without taking the mailbox offline.
    creds = load_credentials(FULL_ENV)
    assert creds.refresh_token == "1//refresh"
    assert not creds.scopes


def test_refresh_failure_carries_no_token_material():
    # google-auth puts the raw token endpoint response into the exception
    # text. Letting that through would print refresh tokens into the poller
    # log on every failure.
    class Exploding:
        def refresh(self, request):
            raise ValueError("invalid_grant: token=1//refresh body=ya29.SECRET")

    with pytest.raises(AuthError) as exc:
        refresh(Exploding(), request=object())
    message = str(exc.value)
    assert "1//refresh" not in message
    assert "ya29" not in message
    assert "SECRET" not in message
