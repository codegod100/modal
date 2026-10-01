import asyncio
import os
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from types import SimpleNamespace
from unittest.mock import AsyncMock

import modal
import pytest

from modal_mcp import _sdk


async def test_explicit_caller_client_is_closed(sdk, monkeypatch):
    monkeypatch.setenv("MODAL_IS_REMOTE", "1")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "host-secret")
    original = dict(os.environ)
    async with _sdk.client_session() as client:
        assert client is sdk.client
    sdk.factory.assert_awaited_once_with("caller-id", "caller-secret")
    sdk.client.__aexit__.assert_awaited_once_with(None, None, None)
    assert dict(os.environ) == original


async def test_client_closed_after_failed_operation(sdk):
    with pytest.raises(ValueError, match="operation failed"):
        async with _sdk.client_session():
            raise ValueError("operation failed")
    sdk.client.__aexit__.assert_awaited_once()


@pytest.mark.parametrize(
    "claims",
    [
        {},
        {"modal_token_id": "id"},
        {"modal_token_id": 1, "modal_token_secret": "secret"},
        {"modal_token_id": "id", "modal_token_secret": ""},
        ["malformed"],
    ],
)
async def test_invalid_authenticated_credentials_never_fall_back(sdk, monkeypatch, claims):
    monkeypatch.setattr(_sdk, "get_access_token", lambda: SimpleNamespace(claims=claims))
    with pytest.raises(PermissionError):
        async with _sdk.client_session():
            pytest.fail("Missing caller credentials must fail closed")
    sdk.factory.assert_not_called()


async def test_missing_http_identity_never_uses_local_profile(sdk, monkeypatch):
    monkeypatch.setattr(_sdk, "get_access_token", lambda: None)

    async def operation():
        async with _sdk.client_session():
            pytest.fail("Unauthenticated HTTP request must fail closed")

    with pytest.raises(PermissionError, match="verified Modal identity"):
        await _sdk.bind_tool(operation, require_auth=True)()
    sdk.factory.assert_not_called()
    assert _sdk._require_auth.get() is False


async def test_token_lookup_error_does_not_fall_back(sdk, monkeypatch):
    def failed_lookup():
        raise RuntimeError("Auth context failure")

    monkeypatch.setattr(_sdk, "get_access_token", failed_lookup)
    with pytest.raises(RuntimeError, match="Auth context failure"):
        async with _sdk.client_session():
            pytest.fail("Auth errors cannot use host credentials")
    sdk.factory.assert_not_called()


async def test_local_stdio_uses_explicit_profile_credentials(sdk, monkeypatch):
    monkeypatch.setattr(_sdk, "get_access_token", lambda: None)
    profile = {"token_id": "local-id", "token_secret": "local-secret", "environment": "dev"}
    monkeypatch.setattr(_sdk, "config", SimpleNamespace(get=profile.get))
    async with _sdk.client_session() as client:
        assert await _sdk.environment_name(client, None) == "dev"
    sdk.factory.assert_awaited_once_with("local-id", "local-secret")
    sdk.workspace_lookup.assert_not_called()


async def test_hosted_default_environment_belongs_to_caller(sdk):
    assert await _sdk.environment_name(sdk.client, None) == "caller-default"
    sdk.workspace_lookup.assert_called_once_with(client=sdk.client)
    assert await _sdk.environment_name(sdk.client, "explicit") == "explicit"


async def test_concurrent_callers_never_share_clients(sdk, monkeypatch):
    identity = ContextVar("test_identity")
    monkeypatch.setattr(
        _sdk,
        "get_access_token",
        lambda: SimpleNamespace(
            claims={
                "modal_token_id": identity.get(),
                "modal_token_secret": identity.get() + "-secret",
            }
        ),
    )
    created = []

    async def factory(token_id, token_secret):
        client = SimpleNamespace(identity=token_id, __aexit__=AsyncMock())
        created.append(client)
        await asyncio.sleep(0)
        return client

    monkeypatch.setattr(modal.Client, "from_credentials", SimpleNamespace(aio=factory))

    async def operation():
        async with _sdk.client_session() as client:
            await asyncio.sleep(0)
            assert client.identity == identity.get()
            return client.identity

    bound = _sdk.bind_tool(operation, require_auth=True)

    async def caller(name):
        identity.set(name)
        return await bound()

    assert await asyncio.gather(caller("alice"), caller("bob")) == ["alice", "bob"]
    assert len(created) == 2 and created[0] is not created[1]
    for client in created:
        client.__aexit__.assert_awaited_once()
    assert _sdk._require_auth.get() is False


def test_sdk_metadata_serialization_preserves_precision():
    class State(Enum):
        DEPLOYED = "deployed"

    @dataclass
    class Item:
        cost: Decimal
        timestamp: datetime
        state: State

    assert _sdk.json_value(
        Item(Decimal("0.123456789"), datetime(2026, 1, 1, tzinfo=timezone.utc), State.DEPLOYED)
    ) == {"cost": "0.123456789", "timestamp": "2026-01-01T00:00:00+00:00", "state": "deployed"}
    with pytest.raises(TypeError, match="Unsupported SDK result"):
        _sdk.json_value(object())
