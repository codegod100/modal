# modal-mcp

An MCP server that exposes [Modal](https://modal.com) workspace management as
tools — and that runs on Modal itself.

26 tools covering apps, functions, logs, containers, sandboxes, volumes,
secrets, and cost.

## Setup

### 1. Modal credentials

```bash
pip install modal
modal token new
```

### 2. Create the secret the deployed server reads

The server authenticates to Modal with a workspace API token, and authenticates
*its own callers* with a bearer token you choose.

Create an API token at https://modal.com/settings/tokens, then:

```bash
export MCP_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
echo "save this, it is your MCP bearer token: $MCP_TOKEN"

modal secret create modal-mcp \
  MODAL_TOKEN_ID=ak-xxxxxxxx \
  MODAL_TOKEN_SECRET=as-xxxxxxxx \
  MCP_AUTH_TOKEN=$MCP_TOKEN
```

### 3. Deploy

```bash
pip install -e .
modal deploy deploy.py
```

Modal prints the URL, e.g. `https://<workspace>--modal-mcp.modal.run`.
The MCP endpoint is that URL + `/mcp`.

### 4. Connect a client

```bash
claude mcp add --transport http modal \
  https://<workspace>--modal-mcp.modal.run/mcp \
  --header "Authorization: Bearer $MCP_TOKEN"
```

Check it's up without a client:

```bash
curl https://<workspace>--modal-mcp.modal.run/health
```

## Running locally instead

Over stdio, using your local `~/.modal.toml` profile — no secret or bearer
token needed:

```bash
claude mcp add modal -- /path/to/modal-mcp/.venv/bin/modal-mcp
```

## Tools

**Workspace** — `whoami`, `list_environments`, `get_workspace_costs`

**Apps** — `list_apps`, `get_app`, `get_app_logs`, `get_deployment_history`,
`stop_app`

**Functions** — `get_function_stats`, `call_function`, `spawn_function`,
`get_function_call_result`, `cancel_function_call`

**Containers** — `list_containers`, `stop_container`

**Sandboxes** — `create_sandbox`, `sandbox_exec`, `list_sandboxes`,
`terminate_sandbox`

**Storage** — `list_volumes`, `list_volume_files`, `read_volume_file`,
`list_secrets`, `create_secret`, `list_dicts`, `list_queues`

Apps are addressable by deployed name (`my-app`) or app ID (`ap-...`).
Tools take an optional `environment`; omitted, they use the server's default.

## Configuration

| Variable | Purpose |
| --- | --- |
| `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` | Workspace credentials the tools act with. Required when deployed. |
| `MCP_AUTH_TOKEN` | Bearer token callers must present. Required when served over HTTP; min 16 chars. |
| `MODAL_ENVIRONMENT` | Default environment for tools that don't name one. |
| `MODAL_MCP_READ_ONLY` | Set to `1` to register only the 16 read-only tools. |

## Notes on the design

- **Credentials.** Inside a Modal container, `Client.from_env()` deliberately
  ignores `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET` and authenticates as the
  container task, which can't make workspace-level calls. The server therefore
  builds its client with `Client.from_credentials`, the supported path for
  acting on behalf of a workspace.
- **Auth is mandatory over HTTP.** A Modal web endpoint is publicly reachable,
  and these tools can stop apps and read secrets metadata. `build_asgi_app()`
  refuses to start without `MCP_AUTH_TOKEN`, rather than defaulting to open.
- **Stateless sessions.** Modal autoscales across containers with no session
  affinity, so the HTTP transport runs in `stateless_http` mode.
- **Secret values are never returned.** `list_secrets` reports names and
  metadata only.
