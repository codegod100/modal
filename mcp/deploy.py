"""Deploy the Modal MCP server to Modal.

    modal deploy deploy.py

No secret and no API token to create. Callers sign in with Modal through the
same device flow as `modal token new`, and each one's own token is what their
SDK requests run with -- so authenticating and being authorized are the same act.

The published URL is derived from the workspace and label below. Override it
with MODAL_MCP_BASE_URL if you deploy under a different name, since OAuth
metadata and the login redirect must be absolute.
"""

import os

import modal

APP_NAME = "modal-mcp"
WORKSPACE = os.environ.get("MODAL_MCP_WORKSPACE", "codegod100")
BASE_URL = os.environ.get("MODAL_MCP_BASE_URL", f"https://{WORKSPACE}--{APP_NAME}.modal.run")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("modal>=1.6,<2", "fastmcp~=4.0")
    .add_local_python_source("modal_mcp")
)

app = modal.App(APP_NAME)


@app.function(
    image=image,
    env={"MODAL_MCP_BASE_URL": BASE_URL},
    # Pending logins and issued tokens live in this process, so every request
    # must reach the same container. Raising this without moving that state to
    # shared storage would make sign-in fail intermittently.
    max_containers=1,
    # Scales to zero, which costs nothing idle but drops sessions when the
    # container goes away -- clients then sign in again. min_containers=1 avoids
    # that, at the price of billing continuously.
    min_containers=0,
    scaledown_window=1200,
    timeout=900,
)
@modal.concurrent(max_inputs=20)
# No requires_proxy_auth: OAuth is the guard, and proxy auth would block the
# discovery and login endpoints that clients must reach unauthenticated.
@modal.asgi_app(label=APP_NAME)
def mcp_server():
    from modal_mcp.server import build_asgi_app

    return build_asgi_app()
