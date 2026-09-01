"""
Regression test for _google_auth.py's handling of Gmail domain-wide
delegation.

Gmail has no "share my mailbox" mechanism the way Drive/Sheets/Calendar
do — a service account can only read/send as a specific person via
domain-wide delegation, which requires each API call to *impersonate*
that person (the "subject") when minting the access token. Before this
fix, `_build_client()`/`_service_account_token()` never threaded a
subject through to `service_account.Credentials.from_service_account_info()`
at all, so every token was minted for the service account's own
(mailbox-less) identity — even though `GmailConnector.meta` already
declared `user_email` as optional_config, implying it should matter.

Found live: a real, completely well-formed service-account key (all
required fields present, domain-wide delegation set up by the tenant's
Workspace admin) still failed every Gmail call with a 400 Bad Request on
GET /gmail/v1/users/me/profile — reproduced directly by calling
GmailConnector.connect() with the tenant's actual stored config, then
root-caused by reading `_google_auth.py` itself: `_service_account_token()`
had no `subject` parameter at all, so `user_email` in the config was
silently never used anywhere.

Drive/Sheets/Calendar must NOT be affected — they're granted access via
a direct per-file/calendar share with the service account's own email,
so impersonating anyone would be wrong for them. Their connectors never
set `user_email` in config, so `subject` stays None and behavior is
unchanged.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from agentix.connectors.builtin._google_auth import _build_client, _service_account_token

_FAKE_KEY = {
    "type": "service_account",
    "project_id": "test-project",
    "private_key_id": "abc123",
    "private_key": "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n",
    "client_email": "agent@test-project.iam.gserviceaccount.com",
    "client_id": "12345",
    "token_uri": "https://oauth2.googleapis.com/token",
}

_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def _mock_credentials():
    """A stand-in for service_account.Credentials that just records the
    subject it was built with and returns a fake token on refresh()."""
    creds = MagicMock()
    creds.token = "fake-token"

    def refresh(_request):
        pass

    creds.refresh.side_effect = refresh
    return creds


def test_service_account_token_passes_subject_for_gmail_impersonation() -> None:
    """Gmail's config carries user_email → the minted token must
    impersonate that mailbox via domain-wide delegation."""
    with patch(
        "google.oauth2.service_account.Credentials.from_service_account_info",
        return_value=_mock_credentials(),
    ) as mock_from_info:
        token = _service_account_token(_FAKE_KEY, _SCOPES, subject="user@workspace.example.com")

    assert token == "fake-token"
    _, kwargs = mock_from_info.call_args
    assert kwargs.get("subject") == "user@workspace.example.com"


def test_service_account_token_no_subject_when_not_impersonating() -> None:
    """Drive/Sheets/Calendar never set user_email — subject must stay
    None so the service account acts as itself (the identity that gets
    directly shared a file/calendar), not some accidentally-impersonated
    mailbox."""
    with patch(
        "google.oauth2.service_account.Credentials.from_service_account_info",
        return_value=_mock_credentials(),
    ) as mock_from_info:
        _service_account_token(_FAKE_KEY, _SCOPES)

    _, kwargs = mock_from_info.call_args
    assert kwargs.get("subject") is None


def test_build_client_threads_user_email_from_config_as_subject() -> None:
    """The real call path: GmailConnector's cfg (with user_email set)
    must reach from_service_account_info as `subject`, not get dropped
    anywhere between _build_client and _service_account_token."""
    cfg = {"credentials_json": _FAKE_KEY, "user_email": "user@workspace.example.com"}

    with patch(
        "google.oauth2.service_account.Credentials.from_service_account_info",
        return_value=_mock_credentials(),
    ) as mock_from_info:
        client = _build_client(cfg, _SCOPES)

    _, kwargs = mock_from_info.call_args
    assert kwargs.get("subject") == "user@workspace.example.com"
    assert client.headers["Authorization"] == "Bearer fake-token"


def test_build_client_no_user_email_means_no_subject() -> None:
    """A Drive/Sheets/Calendar-style cfg with no user_email must not
    accidentally impersonate anyone."""
    cfg = {"credentials_json": _FAKE_KEY}

    with patch(
        "google.oauth2.service_account.Credentials.from_service_account_info",
        return_value=_mock_credentials(),
    ) as mock_from_info:
        _build_client(cfg, _SCOPES)

    _, kwargs = mock_from_info.call_args
    assert kwargs.get("subject") is None
