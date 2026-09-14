# modal-mcp

An MCP server that exposes [Modal](https://modal.com) to an agent by driving the
`modal` CLI rather than reimplementing Modal's API.

Run it locally over stdio against your own CLI login, or deploy it to Modal and
sign in with your Modal account. Either way there is no token to copy around,
and features Modal adds to the CLI are reachable the day they ship.

## Two ways to run it

### Hosted on Modal, sign in with Modal

```bash
cd mcp && pip install -e .
modal deploy deploy.py
```

Then add the printed URL as an MCP server, using the full endpoint path:

```
https://<workspace>--modal-mcp.modal.run/mcp
```

No secret, no API token, nothing to paste. The client discovers the server's
OAuth metadata, registers itself, and sends you to Modal to sign in. Each caller
ends up acting as themselves: their own Modal token is what their commands run
with, so authenticating and being authorized are the same act.

Restrict who may use it by setting `MODAL_MCP_ALLOWED_WORKSPACES` to a
comma-separated list of workspace names. Without it, anyone who completes a
Modal login can connect -- they act as their own account and cannot touch yours,
but they can spend their own Modal compute through your server.

### Locally over stdio

```bash
modal token new                       # one-time
claude mcp add modal -- /path/to/mcp/.venv/bin/modal-mcp
```

No deployment and no auth: the server runs on your machine and inherits your
CLI login from `~/.modal.toml`.

## Tools

**Workspace** — `whoami`, `list_environments`, `get_workspace_costs`
(one billing cycle: `"this month"`, `"last month"`, or `"2026-08"`)

**Apps** — `list_apps`, `get_app`, `get_app_logs`, `get_deployment_history`,
`stop_app`

**Functions** — `get_function_stats`, `call_function`, `spawn_function`,
`get_function_call_result`, `cancel_function_call`

**Containers** — `list_containers`, `stop_container`

**Sandboxes** — `create_sandbox`, `sandbox_exec`, `list_sandboxes`,
`terminate_sandbox`

**Storage** — `list_volumes`, `list_volume_files`, `read_volume_file`,
`list_secrets`, `create_secret`, `list_dicts`, `list_queues`

**Escape hatch** — `modal_cli(args="app list --json")` runs any `modal` command,
including `--help`, for anything without a named tool.

Apps are addressable by deployed name (`my-app`) or app ID (`ap-...`).
Tools take an optional `environment`; omitted, they use the CLI's default.

## How it works

Most tools map onto a CLI command, preferring `--json`:

| Tool | Command |
| --- | --- |
| `list_apps` | `modal app list --json` |
| `get_app_logs` | `modal app logs <app> --since 30m --tail 200` |
| `list_volume_files` | `modal volume ls <vol> <path> --json` |
| `read_volume_file` | `modal volume get <vol> <path> -` |
| `get_workspace_costs` | `modal billing summary --for "this month" --json` |

Sandboxes and calls into deployed Functions have **no CLI subcommand**. Those
tools generate a short Python script and run it with `modal run`, parsing one
sentinel-prefixed JSON line out of the output. It costs a few seconds per call —
measured at ~3s to create a sandbox and ~6s to exec in one — and everything else
is a direct CLI invocation.

A sandbox is created under a looked-up (persistent) app, so it outlives the
ephemeral driver app and `create_sandbox` → `sandbox_exec` → `terminate_sandbox`
works across separate calls.

## Configuration

| Variable | Purpose |
| --- | --- |
| `MODAL_ENVIRONMENT` | Default environment for tools that don't name one. |
| `MODAL_MCP_READ_ONLY` | Set to `1` to register only the 16 read-only tools. |
| `MODAL_MCP_MODAL_BIN` | Path to the `modal` executable, if not beside the running interpreter or on `PATH`. |
| `MODAL_MCP_BASE_URL` | Hosted only: the server's own public URL. OAuth metadata and the login redirect must be absolute. `deploy.py` sets it. |
| `MODAL_MCP_ALLOWED_WORKSPACES` | Hosted only: comma-separated workspaces permitted to sign in. Unset means any Modal user may connect. |

## How sign-in works

Modal has no self-serve OAuth for third parties, but `modal token new` is an
RFC 8628-style device grant: `TokenFlowCreate` returns a modal.com URL, the user
approves in a browser, and `TokenFlowWait` yields their API token and workspace
name. It works with `localhost_port=0`, so no local callback server is needed
and it can be driven from a container.

MCP clients only drive login automatically when the server implements the MCP
authorization spec, so `auth.py` wraps that device flow in a standard OAuth 2.1
server. FastMCP's `InMemoryOAuthProvider` supplies registration, PKCE, codes and
refresh; only the "who is this user" step is replaced. `authorize()` cannot
block for a browser login, so it redirects to a page that sends the user to
Modal and polls until approval lands, then forwards to the client's redirect URI
with an ordinary authorization code.

Two consequences worth knowing:

- **The server holds your Modal token**, in process memory only, never on disk.
  That is inherent to server-managed calls and is what a real refresh token
  would avoid.
- **Sessions do not survive scaledown.** Auth state is in-process, so the
  deployment pins `max_containers=1`; raising it would break sign-in
  intermittently. With `min_containers=0` the container goes away when idle and
  clients sign in again.

## Known limits

- **`get_app` cannot list an app's Functions.** Modal exposes no way to read a
  deployed App's layout: `App.registered_functions` is documented as not working
  for an App fetched via `lookup`, and there is no CLI equivalent. The tool
  returns app metadata, deployment history, and a dashboard URL instead.
- **Sandbox and Function-call tools are slower** than the rest, for the reason
  above.
- **Secret values are never returned.** `list_secrets` reports names only.
