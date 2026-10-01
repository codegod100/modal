"""Request-scoped Modal SDK clients and MCP policy checks."""

import os
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from functools import wraps
from typing import Any

import modal
from fastmcp.server.dependencies import get_access_token
from modal.config import config

_require_auth: ContextVar[bool] = ContextVar("modal_mcp_require_auth", default=False)


def read_only() -> bool:
    return os.environ.get("MODAL_MCP_READ_ONLY", "").lower() in ("1", "true", "yes")


def require_write() -> None:
    if read_only():
        raise PermissionError("This Modal MCP server is read-only.")


def bind_tool(fn, *, require_auth: bool, write: bool = False):
    """Carry HTTP authentication requirements into each tool invocation."""

    @wraps(fn)
    async def bound(*args, **kwargs):
        marker = _require_auth.set(require_auth)
        try:
            if write:
                require_write()
            return await fn(*args, **kwargs)
        finally:
            _require_auth.reset(marker)

    return bound


@asynccontextmanager
async def client_session():
    """Create an explicit SDK client, never the ambient container client.

    HTTP calls require credentials bound to their verified OAuth token. Only
    local stdio calls may read an existing local Modal profile. No token or
    default-client environment variables are changed, including concurrently.
    """
    token = get_access_token()
    if token is not None:
        claims = token.claims or {}
        if not isinstance(claims, dict):
            raise PermissionError("The caller's Modal identity claims are malformed.")
        token_id = claims.get("modal_token_id")
        token_secret = claims.get("modal_token_secret")
    elif _require_auth.get():
        raise PermissionError("A verified Modal identity is required.")
    else:
        token_id = config.get("token_id")
        token_secret = config.get("token_secret")
    if not isinstance(token_id, str) or not token_id:
        raise PermissionError("The caller has no Modal token ID.")
    if not isinstance(token_secret, str) or not token_secret:
        raise PermissionError("The caller has no Modal token secret.")

    client = await modal.Client.from_credentials.aio(token_id, token_secret)
    try:
        yield client
    finally:
        # from_credentials already opens the connection. Entering it a second
        # time would reopen it; use its async context-manager exit to close it.
        await client.__aexit__(None, None, None)


async def environment_name(client, requested: str | None) -> str:
    """Resolve defaults in the caller's workspace, not the server's container."""
    if requested:
        return requested
    if not _require_auth.get() and get_access_token() is None:
        configured = config.get("environment")
        if configured:
            return str(configured)
    workspace = modal.Workspace.from_context(client=client)
    settings = await workspace.settings.list.aio()
    return settings.default_environment


def json_value(value: Any) -> Any:
    """Encode SDK metadata and results without losing decimal precision."""
    if isinstance(value, Enum):
        return json_value(value.value)
    if is_dataclass(value) and not isinstance(value, type):
        return json_value(asdict(value))
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Unsupported SDK result type: {type(value).__name__}")
