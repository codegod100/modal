"""FastMCP server exposing caller-scoped Modal Python SDK operations."""

import os

from fastmcp import FastMCP

from . import tools
from ._sdk import bind_tool, read_only

INSTRUCTIONS = """\
Tools for inspecting and operating a Modal.com workspace through the public
Modal Python SDK: apps, functions, logs, sandboxes, storage, billing and typed
HTTP service deployment. Every operation uses the authenticated caller's client.

Start with `whoami` to confirm which workspace and environment you are acting
on. Most tools take an optional `environment`; omitted, hosted calls use the
caller's workspace default and local stdio honors the local profile configuration.

Live apps can be addressed by their deployed name (e.g. "my-app") or app ID
(e.g. "ap-..."). Use `list_apps` to discover both. Stopped apps, historical
deployment versions and arbitrary container management are not exposed.

Deploy an HTTP service with deploy_service using a container image, argv and
port. This creates or updates the named app and requires Modal proxy auth for
its endpoint. deploy_web_function takes the same inputs and deploys a web
function instead; with public=true its endpoint needs no proxy auth, so use it
for websites. Both take an optional gpu (e.g. "L4", "H100:2") to run each
container on a GPU. For GPU or batch work without an HTTP endpoint, use
deploy_command_function: it deploys a function named run that executes argv per
call, invoked with call_function under the caller's own credentials (no proxy
token). All three take volumes ({mount path: Volume name}, created if missing),
secrets (Secret names exposed as environment variables) and image_commands
(shell commands run on Modal during the image build, e.g. to compile a service
that has no registry image). None of them accepts Python source or executes
commands locally.
Sandbox commands run inside the selected sandbox through the SDK.
"""

# Tools that only read state.
READ_TOOLS = [
    tools.whoami,
    tools.list_environments,
    tools.get_workspace_costs,
    tools.list_apps,
    tools.get_app,
    tools.get_app_logs,
    tools.get_function_stats,
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
    tools.deploy_service,
    tools.deploy_web_function,
    tools.deploy_command_function,
]


def build_mcp(auth=None) -> FastMCP:
    mcp = FastMCP(name="modal", instructions=INSTRUCTIONS, auth=auth)
    for fn in READ_TOOLS:
        mcp.tool(bind_tool(fn, require_auth=auth is not None))
    if not read_only():
        for fn in WRITE_TOOLS:
            mcp.tool(bind_tool(fn, require_auth=auth is not None, write=True))
    return mcp


def _state_store():
    """Durable sign-in storage, if the deployment provides it.

    MODAL_MCP_STATE_DIR is a directory that outlives the container (a mounted
    Modal Volume); MODAL_MCP_STATE_VOLUME names that volume so each write is
    committed right away rather than whenever the container happens to exit.
    Without the directory, sign-ins live in memory and end with the container.
    """
    from .auth import JsonFileStore

    state_dir = os.environ.get("MODAL_MCP_STATE_DIR", "")
    if not state_dir:
        return None
    commit = None
    volume_name = os.environ.get("MODAL_MCP_STATE_VOLUME", "")
    if volume_name:
        import modal

        volume = modal.Volume.from_name(volume_name)

        async def commit():
            await volume.commit.aio()

    return JsonFileStore(os.path.join(state_dir, "auth-state.json"), commit=commit)


def build_asgi_app(base_url: str | None = None):
    """ASGI app for remote serving, authenticated by signing in with Modal.

    Callers run through an OAuth flow whose login step is Modal's device token
    flow, so each one ends up acting as themselves: their own Modal token is
    what the SDK client runs with. There is no shared secret and no allowlist to keep
    in sync -- authenticating *is* being that Modal user.

    Requires MODAL_MCP_BASE_URL, the server's own public URL, because OAuth
    metadata and the login redirect have to be absolute.
    """
    from .auth import ModalTokenFlowProvider

    base_url = base_url or os.environ.get("MODAL_MCP_BASE_URL", "")
    if not base_url:
        raise RuntimeError(
            "MODAL_MCP_BASE_URL must be set to this server's public URL "
            "(e.g. https://workspace--modal-mcp.modal.run)."
        )
    allowed = [
        w for w in os.environ.get("MODAL_MCP_ALLOWED_WORKSPACES", "").split(",") if w.strip()
    ]
    auth = ModalTokenFlowProvider(
        base_url=base_url, allowed_workspaces=allowed, state_store=_state_store()
    )
    mcp = build_mcp(auth=auth)
    # Deliberately NOT stateless: stateless mode drops the GET route on /mcp, so
    # a client probing with GET gets a bare 405 with no WWW-Authenticate and
    # cannot discover how to sign in. Auth state already pins this deployment to
    # one container (see app.py), so sessions cost nothing extra.
    app = mcp.http_app(path="/mcp")

    # Clients are often given the bare origin rather than the full endpoint, and
    # probe "/" directly. Redirect instead of 404ing: 307 preserves the method
    # and body, so a POSTed MCP request survives the hop.
    from starlette.responses import RedirectResponse
    from starlette.routing import Route

    async def _root(request):
        return RedirectResponse("/mcp", status_code=307)

    app.router.routes.append(Route("/", _root, methods=["GET", "POST", "DELETE", "OPTIONS"]))
    return app


def main() -> None:
    """Entry point: serve over stdio using the local Modal profile's SDK client."""
    build_mcp().run()


if __name__ == "__main__":
    main()
