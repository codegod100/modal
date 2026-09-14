"""Deploy the Modal MCP server to Modal itself.

    modal deploy deploy.py

Requires a Modal secret named `modal-mcp` holding:
    MODAL_TOKEN_ID      workspace API token id      (modal token new / dashboard)
    MODAL_TOKEN_SECRET  workspace API token secret
    MCP_AUTH_TOKEN      bearer token MCP clients must present

See README.md for the one-liner that creates it.
"""

import modal

from modal_mcp.server import build_asgi_app

APP_NAME = "modal-mcp"
SECRET_NAME = "modal-mcp"

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("modal~=1.5", "fastmcp~=4.0")
    .add_local_python_source("modal_mcp")
)

app = modal.App(APP_NAME)

secret = modal.Secret.from_name(
    SECRET_NAME,
    required_keys=["MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET", "MCP_AUTH_TOKEN"],
)


@app.function(
    image=image,
    secrets=[secret],
    # A warm container keeps tool latency low; drop to 0 to pay only per request.
    min_containers=1,
    scaledown_window=300,
    timeout=900,
)
@modal.concurrent(max_inputs=20)
@modal.asgi_app(label=APP_NAME)
def mcp_server():
    return build_asgi_app()
