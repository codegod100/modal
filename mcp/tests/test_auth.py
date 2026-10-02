from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider

from modal_mcp.auth import ModalCredentials, ModalTokenFlowProvider


@pytest.fixture
def provider():
    return ModalTokenFlowProvider(base_url="https://example.test")


def test_credential_repr_never_exposes_token_material():
    credentials = ModalCredentials("dummy-id", "dummy-secret", "caller")
    assert repr(credentials) == "ModalCredentials(workspace='caller')"
    assert not hasattr(credentials, "env")


async def test_verified_access_token_carries_only_its_own_identity(provider, monkeypatch):
    access = NS(claims={"existing": "claim"})
    parent = AsyncMock(return_value=access)
    monkeypatch.setattr(InMemoryOAuthProvider, "load_access_token", parent)
    provider._credentials["oauth-access"] = ModalCredentials("caller-id", "caller-secret", "caller")
    result = await provider.load_access_token("oauth-access")
    assert result.claims == {
        "existing": "claim",
        "workspace": "caller",
        "modal_token_id": "caller-id",
        "modal_token_secret": "caller-secret",
    }
    parent.assert_awaited_once_with("oauth-access")


async def test_access_token_without_modal_identity_is_rejected(provider, monkeypatch):
    monkeypatch.setattr(
        InMemoryOAuthProvider, "load_access_token", AsyncMock(return_value=NS(claims={}))
    )
    assert await provider.load_access_token("unbound") is None
    monkeypatch.setattr(InMemoryOAuthProvider, "load_access_token", AsyncMock(return_value=None))
    assert await provider.load_access_token("invalid") is None


async def test_authorization_exchange_binds_credentials_after_validation(provider, monkeypatch):
    credentials = ModalCredentials("caller-id", "caller-secret", "caller")
    provider._credentials["code"] = credentials
    token = NS(access_token="access", refresh_token="refresh")
    monkeypatch.setattr(
        InMemoryOAuthProvider, "exchange_authorization_code", AsyncMock(return_value=token)
    )
    assert await provider.exchange_authorization_code(NS(), NS(code="code")) is token
    assert "code" not in provider._credentials
    assert provider.credentials_for("access") is credentials
    assert provider.credentials_for("refresh") is credentials


async def test_failed_code_exchange_does_not_consume_credentials(provider, monkeypatch):
    credentials = ModalCredentials("caller-id", "caller-secret", "caller")
    provider._credentials["code"] = credentials
    monkeypatch.setattr(
        InMemoryOAuthProvider,
        "exchange_authorization_code",
        AsyncMock(side_effect=ValueError("invalid exchange")),
    )
    with pytest.raises(ValueError):
        await provider.exchange_authorization_code(NS(), NS(code="code"))
    assert provider._credentials == {"code": credentials}


async def test_refresh_retains_the_original_modal_identity(provider, monkeypatch):
    credentials = ModalCredentials("caller-id", "caller-secret", "caller")
    provider._credentials["old-refresh"] = credentials
    token = NS(access_token="new-access", refresh_token="new-refresh")
    monkeypatch.setattr(
        InMemoryOAuthProvider, "exchange_refresh_token", AsyncMock(return_value=token)
    )
    await provider.exchange_refresh_token(NS(), NS(token="old-refresh"), [])
    assert provider.credentials_for("new-access") is credentials
    assert provider.credentials_for("new-refresh") is credentials


# -- persistence -----------------------------------------------------------

from mcp.server.auth.provider import AuthorizationParams  # noqa: E402
from mcp.shared.auth import OAuthClientInformationFull  # noqa: E402

from modal_mcp.auth import JsonFileStore  # noqa: E402


async def _sign_in(provider, credentials):
    """Register a client and complete a login without the Modal token flow."""
    client = OAuthClientInformationFull(
        client_id="mcp-client",
        redirect_uris=["https://client.test/callback"],
        token_endpoint_auth_method="none",
    )
    await provider.register_client(client)
    params = AuthorizationParams(
        state="s",
        scopes=[],
        code_challenge="c" * 43,
        redirect_uri="https://client.test/callback",
        redirect_uri_provided_explicitly=True,
    )
    redirect = await InMemoryOAuthProvider.authorize(provider, client, params)
    code = redirect.split("code=", 1)[1].split("&", 1)[0]
    provider._credentials[code] = credentials
    loaded = await provider.load_authorization_code(client, code)
    return client, await provider.exchange_authorization_code(client, loaded)


async def test_sign_in_survives_a_restart(tmp_path):
    commits = AsyncMock()
    store = JsonFileStore(tmp_path / "auth.json", commit=commits)
    first = ModalTokenFlowProvider(base_url="https://example.test", state_store=store)
    client, token = await _sign_in(first, ModalCredentials("caller-id", "caller-secret", "caller"))
    assert commits.await_count >= 2

    # A fresh process sharing only the file: the client stays registered and the
    # issued tokens keep working with the same Modal identity.
    second = ModalTokenFlowProvider(base_url="https://example.test", state_store=store)
    assert await second.get_client("mcp-client") is not None
    access = await second.load_access_token(token.access_token)
    assert access.claims["modal_token_id"] == "caller-id"

    refresh = await second.load_refresh_token(client, token.refresh_token)
    renewed = await second.exchange_refresh_token(client, refresh, [])
    third = ModalTokenFlowProvider(base_url="https://example.test", state_store=store)
    assert (await third.load_access_token(renewed.access_token)).claims["workspace"] == "caller"
    assert await third.load_access_token(token.access_token) is None


async def test_rotated_tokens_leave_no_stored_credentials(tmp_path):
    store = JsonFileStore(tmp_path / "auth.json")
    provider = ModalTokenFlowProvider(base_url="https://example.test", state_store=store)
    client, token = await _sign_in(provider, ModalCredentials("caller-id", "caller-secret", "c"))
    refresh = await provider.load_refresh_token(client, token.refresh_token)
    renewed = await provider.exchange_refresh_token(client, refresh, [])
    stored = (await store.load())["credentials"]
    assert set(stored) == {renewed.access_token, renewed.refresh_token}
    assert oct((tmp_path / "auth.json").stat().st_mode & 0o777) == "0o600"


async def test_unreadable_state_starts_empty_instead_of_failing(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text("{not json")
    provider = ModalTokenFlowProvider(
        base_url="https://example.test", state_store=JsonFileStore(path)
    )
    assert await provider.get_client("anyone") is None


async def test_without_a_store_nothing_is_written(provider, tmp_path):
    await _sign_in(provider, ModalCredentials("caller-id", "caller-secret", "caller"))
    assert list(tmp_path.iterdir()) == []


async def test_expired_access_token_leaves_refresh_usable(provider):
    client, token = await _sign_in(provider, ModalCredentials("caller-id", "caller-secret", "c"))
    provider.access_tokens[token.access_token].expires_at = 1
    assert await provider.load_access_token(token.access_token) is None
    refresh = await provider.load_refresh_token(client, token.refresh_token)
    assert refresh is not None
    renewed = await provider.exchange_refresh_token(client, refresh, [])
    assert (await provider.load_access_token(renewed.access_token)).claims["workspace"] == "c"
