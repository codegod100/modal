"""FastMCP server exposing Modal.com management tools."""

import hmac
import json
import os

from fastmcp import FastMCP

from . import tools
from ._client import read_only

INSTRUCTIONS = """\
Tools for inspecting and operating a Modal.com workspace: apps, functions,
logs, containers, sandboxes, volumes, secrets and cost.

Start with `whoami` to confirm which workspace and environment you are acting
on. Most tools take an optional `environment` argument; when omitted they use
the server's default environment.

Apps can be addressed either by their deployed name (e.g. "my-app") or by their
app ID (e.g. "ap-..."). Use `list_apps` to discover both.
"""

# Tools that only read state.
READ_TOOLS = [
    tools.whoami,
    tools.list_environments,
    tools.get_workspace_costs,
    tools.list_apps,
    tools.get_app,
    tools.get_app_logs,
    tools.get_deployment_history,
    tools.get_function_stats,
    tools.list_containers,
    tools.list_sandboxes,
    tools.list_volumes,
    tools.list_volume_files,
    tools.read_volume_file,
    tools.list_secrets,
    tools.list_dicts,
    tools.list_queues,
]

# Tools that change state or spend compute.
WRITE_TOOLS = [
    tools.call_function,
    tools.spawn_function,
    tools.get_function_call_result,
    tools.cancel_function_call,
    tools.create_sandbox,
    tools.sandbox_exec,
    tools.terminate_sandbox,
    tools.create_secret,
    tools.stop_app,
    tools.stop_container,
]


def build_mcp() -> FastMCP:
    mcp = FastMCP(name="modal", instructions=INSTRUCTIONS)
    for fn in READ_TOOLS:
        mcp.tool(fn)
    if not read_only():
        for fn in WRITE_TOOLS:
            mcp.tool(fn)
    return mcp


class BearerAuth:
    """Minimal ASGI guard requiring `Authorization: Bearer <token>`.

    Wrapping the ASGI app directly (rather than adding a Starlette middleware)
    keeps the lifespan scope untouched, which Modal's asgi_app relies on.
    """

    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path.rstrip("/") == "/health":
            await _json_response(send, 200, {"status": "ok"})
            return

        header = ""
        for key, value in scope.get("headers", []):
            if key == b"authorization":
                header = value.decode("latin-1")
                break

        scheme, _, presented = header.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            presented.strip(), self.token
        ):
            await _json_response(
                send,
                401,
                {"error": "unauthorized"},
                extra_headers=[(b"www-authenticate", b"Bearer")],
            )
            return

        await self.app(scope, receive, send)


async def _json_response(send, status: int, body: dict, extra_headers=None):
    payload = json.dumps(body).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(payload)).encode()),
    ]
    headers.extend(extra_headers or [])
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": payload})


def build_asgi_app():
    """ASGI app for remote (HTTP) serving.

    These tools can run arbitrary code in the workspace (create_sandbox +
    sandbox_exec) and spend money, so the endpoint is never served open. One of
    two guards must be in place:

    * ``MODAL_MCP_TRUST_PROXY_AUTH=1`` -- something in front already
      authenticates. On Modal that is ``requires_proxy_auth=True``, which
      rejects unauthorized requests at the edge, so they never even start a
      container. ``deploy.py`` sets both together.
    * ``MCP_AUTH_TOKEN`` -- a bearer token this app checks itself, for hosting
      where no such proxy exists.
    """
    token = os.environ.get("MCP_AUTH_TOKEN", "")
    trust_proxy = os.environ.get("MODAL_MCP_TRUST_PROXY_AUTH", "").lower() in (
        "1",
        "true",
        "yes",
    )
    if not trust_proxy and len(token) < 16:
        raise RuntimeError(
            "Refusing to serve an unauthenticated MCP endpoint. Either deploy behind "
            "Modal proxy auth (requires_proxy_auth=True, plus "
            "MODAL_MCP_TRUST_PROXY_AUTH=1), or set MCP_AUTH_TOKEN to a secret of at "
            "least 16 characters."
        )
    mcp = build_mcp()
    # Modal autoscales across containers, so HTTP sessions must not be sticky.
    app = mcp.http_app(path="/mcp", stateless_http=True, json_response=True)
    return BearerAuth(app, token) if token else app


def main() -> None:
    """Entry point for local stdio use."""
    build_mcp().run()


if __name__ == "__main__":
    main()
