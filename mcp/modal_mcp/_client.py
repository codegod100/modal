"""Modal API client management for the MCP server.

Credentials resolve in three ways, in order:

1. ``MODAL_TOKEN_ID`` / ``MODAL_TOKEN_SECRET``, used to act as a specific
   workspace from anywhere. Only needed to act as a *different* workspace than
   the one the server runs in.
2. Otherwise ``Client.from_env()``. Inside a Modal container this authenticates
   as the container's own task identity, which carries the permissions of the
   workspace the app is deployed in -- verified to cover both reads (AppList,
   VolumeList, SecretList) and writes (Dict create/delete, Sandbox
   create/exec/terminate). This is why a deployed server needs no token.
3. Locally, ``Client.from_env()`` reads the ``~/.modal.toml`` profile that
   ``modal token new`` writes, so the CLI login is enough.

Note that inside a container ``from_env()`` deliberately *ignores* the token
environment variables, so case 1 must construct the client explicitly with
``_Client.from_credentials`` rather than setting env vars and hoping.
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
        # Explicit tokens are optional: they only matter when the server should
        # act as a workspace other than the one it runs in.
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
                    "No Modal credentials. Run `modal token new` for a local profile, "
                    "or set MODAL_TOKEN_ID and MODAL_TOKEN_SECRET. When deployed on "
                    "Modal the container's own identity is used and neither is needed."
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
