"""All tests are offline; accidental network/process calls fail immediately."""

import asyncio
import socket
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import modal
import pytest

from modal_mcp import _sdk


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline tests must not access the network or start a process")

    async def forbidden_async(*args, **kwargs):
        forbidden()

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden_async)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", forbidden_async)
    monkeypatch.setattr(modal.Client, "from_env", SimpleNamespace(aio=forbidden_async))
    monkeypatch.delenv("MODAL_MCP_READ_ONLY", raising=False)


@pytest.fixture
def sdk(monkeypatch):
    client = SimpleNamespace(__aexit__=AsyncMock())
    factory = AsyncMock(return_value=client)
    monkeypatch.setattr(modal.Client, "from_credentials", SimpleNamespace(aio=factory))
    monkeypatch.setattr(
        _sdk,
        "get_access_token",
        lambda: SimpleNamespace(
            claims={"modal_token_id": "caller-id", "modal_token_secret": "caller-secret"}
        ),
    )
    monkeypatch.setattr(
        _sdk,
        "config",
        SimpleNamespace(
            get=Mock(
                side_effect=AssertionError(
                    "Hosted requests must not read ambient profile credentials"
                )
            )
        ),
    )
    workspace = SimpleNamespace(
        name="caller",
        hydrate=SimpleNamespace(aio=AsyncMock()),
        settings=SimpleNamespace(
            list=SimpleNamespace(
                aio=AsyncMock(return_value=SimpleNamespace(default_environment="caller-default"))
            )
        ),
        billing=SimpleNamespace(summary=SimpleNamespace(aio=AsyncMock(return_value={}))),
    )
    lookup = Mock(return_value=workspace)
    monkeypatch.setattr(modal.Workspace, "from_context", lookup)
    return SimpleNamespace(
        client=client, factory=factory, workspace=workspace, workspace_lookup=lookup
    )
