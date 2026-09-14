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

The deployed server needs **no Modal API token**: the container authenticates
with its own task identity, which carries the permissions of the workspace the
app is deployed in. The only secret it needs is the bearer token that
authenticates *its own callers*.

```bash
export MCP_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
echo "save this, it is your MCP bearer token: $MCP_TOKEN"

modal secret create modal-mcp MCP_AUTH_TOKEN=$MCP_TOKEN
```

Add `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` to that secret only if you want the
server to act as a *different* workspace than the one it runs in.

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

Apps are addressable by deployed name (`my-app`) or app ID (`ap-...`).
Tools take an optional `environment`; omitted, they use the server's default.

Two behaviours worth knowing:

- **Nothing blocks past 120s.** Modal caps web requests at 150s, so
  `call_function` spawns the call and hands back a `function_call_id` with
  status `pending` if it runs long — poll `get_function_call_result` rather than
  losing the run. `sandbox_exec` is capped the same way.
- **Sandboxes reap themselves.** `create_sandbox` defaults to a 600s lifetime and
  a 300s idle timeout, since a forgotten sandbox bills until it stops.

## Configuration

| Variable | Purpose |
| --- | --- |
| `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` | Optional. Act as a specific workspace. Unset, the server uses the container's own identity when deployed, or your `~/.modal.toml` profile locally. |
| `MCP_AUTH_TOKEN` | Bearer token callers must present. Required when served over HTTP; min 16 chars. |
| `MODAL_ENVIRONMENT` | Default environment for tools that don't name one. |
| `MODAL_MCP_READ_ONLY` | Set to `1` to register only the 16 read-only tools. |

## Cost

The deployed server scales to zero (`min_containers=0` in `deploy.py`), so an
idle server costs nothing and the first call after a lull pays a cold start. Set
`min_containers=1` to keep one warm — that bills continuously, so it's opt-in.

## Notes on the design

- **No API token needed when deployed.** A container authenticates with its own
  task identity, which carries the permissions of the workspace the app runs in
  — verified against a live workspace for both reads (`AppList`, `VolumeList`,
  `SecretList`) and writes (Dict create/delete, Sandbox create/exec/terminate).
  Tokens stay supported for acting as a different workspace, and in that case
  the client must be built with `Client.from_credentials`, since `from_env()`
  deliberately ignores those variables inside a container.
- **Auth is mandatory over HTTP.** A Modal web endpoint is publicly reachable,
  and these tools can stop apps and read secrets metadata. `build_asgi_app()`
  refuses to start without `MCP_AUTH_TOKEN`, rather than defaulting to open.
- **Stateless sessions.** Modal autoscales across containers with no session
  affinity, so the HTTP transport runs in `stateless_http` mode.
- **Secret values are never returned.** `list_secrets` reports names and
  metadata only.
- **Internal async API, deliberately.** The tools use Modal's `_Client` layer
  rather than the public `.aio` interface. Most tools here are workspace
  management calls with no public SDK equivalent, so they must go through
  `client.stub` — the same path Modal's own CLI takes. The two layers cannot be
  mixed: public `.aio` wrappers run on Modal's synchronizer loop, and passing our
  client into one deadlocks, while a public client's raw `stub` calls lose
  request cancellation. `modal_mcp/tools.py` documents this at the top.
