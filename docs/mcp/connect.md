# Connect

## Hosted server

The server is deployed at:

```
https://codegod100--modal-mcp.modal.run/mcp
```

On first use your client opens a browser to sign in with Modal (the same device
flow as `modal token new`). Each caller's tools then run with their own
credentials.

=== "Claude Code"

    ```bash
    claude mcp add --transport http modal https://codegod100--modal-mcp.modal.run/mcp
    ```

    Then run `/mcp` inside Claude Code to authenticate.

=== "claude.ai custom connector"

    1. Open **Settings → Connectors → Add custom connector**.
    2. Name it `modal` and paste `https://codegod100--modal-mcp.modal.run/mcp`
       as the URL.
    3. Click **Connect** and sign in with Modal.

    The connector is then available in chats, projects and Claude Code on the
    web.

Start with the `whoami` tool to confirm which workspace and environment you are
acting on.

!!! note "Staying signed in"
    Sign-ins are stored on the `modal-mcp-auth-state` Volume and restored when a
    new container starts, so you stay signed in across scale-to-zero and
    redeploys. See [Deploy](deploy.md#auth-persistence).

## Local stdio

```bash
cd mcp
pip install -e .
modal token new  # if you do not already have a Modal profile
claude mcp add modal -- /path/to/mcp/.venv/bin/modal-mcp
```

The Modal CLI above is only for developer setup. Tool operations use SDK clients
constructed explicitly from the selected profile's credentials.

## Read-only mode

Set `MODAL_MCP_READ_ONLY=1` (also `true` or `yes`) to register only the
[read tools](tools.md#read-tools). Write implementations also enforce the
policy if called directly.
