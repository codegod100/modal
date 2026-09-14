"""Modal API client management for the MCP server.

Inside a Modal container ``Client.from_env()`` deliberately ignores
``MODAL_TOKEN_ID`` / ``MODAL_TOKEN_SECRET`` and authenticates as the container
task, which cannot make workspace-level calls such as ``AppList``. So when
tokens are present we always build the client explicitly with
``_Client.from_credentials``, which is the supported way to act on behalf of a
workspace from anywhere.
"""

import asyncio
import os

from modal.client import _Client

_client: _Client | None = None
_lock: asyncio.Lock | None = None


class ConfigError(RuntimeError):
    """Raised when the server is missing the credentials it needs."""


def _lock_for_loop() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


async def get_client() -> _Client:
    """Return a cached, authenticated Modal client."""
    global _client
    if _client is not None:
        return _client
    async with _lock_for_loop():
        if _client is not None:
            return _client
        token_id = os.environ.get("MODAL_TOKEN_ID")
        token_secret = os.environ.get("MODAL_TOKEN_SECRET")
        if token_id and token_secret:
            _client = await _Client.from_credentials(token_id, token_secret)
        else:
            # Local use with a ~/.modal.toml profile.
            try:
                _client = await _Client.from_env()
            except Exception as exc:
                raise ConfigError(
                    "No Modal credentials. Set MODAL_TOKEN_ID and MODAL_TOKEN_SECRET, "
                    "or run `modal token new` to create a local profile."
                ) from exc
        return _client


def default_environment() -> str | None:
    """Environment name to use when a tool call does not specify one."""
    return os.environ.get("MODAL_ENVIRONMENT") or None


def env_or_default(environment: str | None) -> str:
    """Normalize an environment argument into the string the API expects."""
    return environment or default_environment() or ""


def read_only() -> bool:
    return os.environ.get("MODAL_MCP_READ_ONLY", "").lower() in ("1", "true", "yes")
