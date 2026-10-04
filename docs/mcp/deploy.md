# Deploy

```bash
cd mcp
pip install -e .
modal deploy app.py
```

No secret or API token needs creating. Callers sign in with Modal, and each
caller's own token is what their SDK requests run with, so authenticating and
being authorized are the same act.

## Use a modal client of 1.6 or newer

!!! warning
    Modal mounts the **deploying client's** own `modal` package into the
    container, shadowing the `modal>=1.6` in the image's `pip_install`. The
    server therefore runs whatever version deployed it. The tools use 1.6-only
    APIs (`Environment.apps`, `App.info`), so `app.py` refuses to deploy from an
    older client.

```bash
pip install -U 'modal>=1.6,<2'
```

## URL and workspace

The app is named `modal-mcp`, and its URL is derived from the workspace:

```
https://<workspace>--modal-mcp.modal.run/mcp
```

| Variable | Default | Purpose |
| --- | --- | --- |
| `MODAL_MCP_WORKSPACE` | `codegod100` | Workspace used to build the base URL |
| `MODAL_MCP_BASE_URL` | `https://<workspace>--modal-mcp.modal.run` | Override when deploying under another name; OAuth metadata and the login redirect must be absolute |
| `MODAL_MCP_ALLOWED_WORKSPACES` | unset | Restrict which workspaces may sign in |
| `MODAL_MCP_READ_ONLY` | unset | Register read tools only |

## Auth persistence

Sign-ins (registered clients, issued tokens and the Modal tokens behind them)
are saved to the `modal-mcp-auth-state` Volume, mounted at `/state`, and
restored when a new container starts. Callers stay signed in across scale-down
and redeploys.

!!! danger "That volume holds callers' Modal tokens"
    Anyone who can read `modal-mcp-auth-state` can act as the people who signed
    in. Treat workspace access accordingly.

`max_containers=1` keeps every request on the same process, because pending
logins and the live token tables are held in memory and only read back from the
volume at startup. Raising it would make sign-in fail intermittently.

## About the login flow

Browser login uses Modal's internal `TokenFlowCreate` and `TokenFlowWait`
device-grant RPCs. These are **not** a supported public SDK login API and remain
a compatibility dependency of hosted login. Tools themselves use no internal
RPCs. Migrating login to Modal-issued OAuth client credentials is separate work.
