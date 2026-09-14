"""Deploy the Modal MCP server to Modal itself.

    modal deploy deploy.py

Needs no secret and no Modal API token:

* The container authenticates to Modal with its own task identity, which carries
  the permissions of the workspace the app is deployed in.
* Callers are authenticated by Modal proxy auth (`requires_proxy_auth=True`),
  which rejects unauthorized requests at the edge, before a container starts.
  Create a token with `modal workspace proxy-tokens create`.

See README.md for connecting a client.
"""

import modal

APP_NAME = "modal-mcp"

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("modal~=1.5", "fastmcp~=4.0")
    .add_local_python_source("modal_mcp")
)

app = modal.App(APP_NAME)


@app.function(
    image=image,
    # Tells the app that something in front of it authenticates callers. Kept next
    # to requires_proxy_auth below so the two cannot drift apart.
    env={"MODAL_MCP_TRUST_PROXY_AUTH": "1"},
    # Scales to zero: an idle server costs nothing, at the price of a cold start
    # on the first call. Set min_containers=1 to keep one warm — that bills
    # continuously, so it is opt-in.
    min_containers=0,
    scaledown_window=300,
    # Modal caps any web request at 150s regardless of this value; the tools keep
    # their own waits under that. The headroom is for slow cold starts.
    timeout=900,
)
@modal.concurrent(max_inputs=20)
@modal.asgi_app(label=APP_NAME, requires_proxy_auth=True)
def mcp_server():
    # Imported here rather than at module scope so that deploying only needs
    # `modal` installed locally, not the server's own dependencies.
    from modal_mcp.server import build_asgi_app

    return build_asgi_app()
