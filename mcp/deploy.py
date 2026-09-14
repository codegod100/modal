"""Deploy the Modal MCP server to Modal itself.

    modal deploy deploy.py

Requires a Modal secret named `modal-mcp` holding:
    MCP_AUTH_TOKEN      bearer token MCP clients must present

No Modal API token is needed. The container authenticates to Modal with its own
task identity, which carries the permissions of the workspace the app is
deployed in. Add MODAL_TOKEN_ID / MODAL_TOKEN_SECRET to the secret only to make
the server act as a *different* workspace.

See README.md for the one-liner that creates it.
"""

import modal

APP_NAME = "modal-mcp"
SECRET_NAME = "modal-mcp"

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("modal~=1.5", "fastmcp~=4.0")
    .add_local_python_source("modal_mcp")
)

app = modal.App(APP_NAME)

secret = modal.Secret.from_name(SECRET_NAME, required_keys=["MCP_AUTH_TOKEN"])


@app.function(
    image=image,
    secrets=[secret],
    # Scales to zero: an idle server costs nothing, at the price of a cold start
    # on the first call. Set min_containers=1 to keep one warm instead — that
    # bills continuously, so it is opt-in.
    min_containers=0,
    scaledown_window=300,
    # Modal caps any web request at 150s regardless of this value; the tools keep
    # their own waits under that. The headroom is for slow cold starts.
    timeout=900,
)
@modal.concurrent(max_inputs=20)
@modal.asgi_app(label=APP_NAME)
def mcp_server():
    # Imported here rather than at module scope so that deploying only needs
    # `modal` installed locally, not the server's own dependencies.
    from modal_mcp.server import build_asgi_app

    return build_asgi_app()
