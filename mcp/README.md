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

### 2. Deploy

```bash
pip install -e .
modal deploy deploy.py
```

No secret and no Modal API token are needed. The container authenticates to
Modal with its own task identity, which carries the permissions of the
workspace it is deployed in.

Modal prints the URL, e.g. `https://<workspace>--modal-mcp.modal.run`.
The MCP endpoint is that URL + `/mcp`.

### 3. Create a proxy token for callers

The endpoint is deployed with `requires_proxy_auth=True`, so Modal rejects
unauthorized requests at the edge — they never reach a container, and never
cost anything.

```bash
modal workspace proxy-tokens create
```

That prints a token id (`wk-...`) and secret (`ws-...`). The secret is shown
once.

### 4. Connect a client

Modal accepts the token pair as a single bearer header, joined with a period —
the same shape MCP clients already send:

```bash
claude mcp add --transport http modal \
  https://<workspace>--modal-mcp.modal.run/mcp \
  --header "Authorization: Bearer wk-xxxx.ws-xxxx"
```

Check the guard is up — this should return 401:

```bash
curl -i https://<workspace>--modal-mcp.modal.run/health
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
| `MCP_AUTH_TOKEN` | Bearer token this app checks itself. Only needed when hosting somewhere without an authenticating proxy in front. Min 16 chars. |
| `MODAL_MCP_TRUST_PROXY_AUTH` | Set to `1` when a proxy already authenticates callers. `deploy.py` sets it alongside `requires_proxy_auth=True`. |
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
- **Auth is mandatory over HTTP.** A Modal web function is public by default,
  and these tools can run arbitrary code in the workspace (`create_sandbox` +
  `sandbox_exec`) and spend money. `build_asgi_app()` refuses to start unless
  either Modal proxy auth is trusted or `MCP_AUTH_TOKEN` is set — it never
  defaults to open. Proxy auth is preferred: Modal manages and revokes the
  tokens, and rejects bad requests at the edge, so a scanner hitting the URL
  never starts a container.
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
