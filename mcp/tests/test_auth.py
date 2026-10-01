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
