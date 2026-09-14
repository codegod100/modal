# modal-mcp

An MCP server that exposes [Modal](https://modal.com) to an agent by driving the
`modal` CLI already installed and authenticated on this machine.

There is no API client here and nothing to deploy. The server shells out to the
CLI, so there are no tokens to manage, and features Modal adds to the CLI are
reachable the day they ship.

## Setup

```bash
pip install modal          # if you don't have it
modal token new            # one-time, opens a browser

cd mcp && pip install -e .
claude mcp add modal -- /path/to/mcp/.venv/bin/modal-mcp
```

That's it. No deployment, no secret, no bearer token — the server runs locally
over stdio and inherits your CLI login from `~/.modal.toml`.

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

## Known limits

- **`get_app` cannot list an app's Functions.** Modal exposes no way to read a
  deployed App's layout: `App.registered_functions` is documented as not working
  for an App fetched via `lookup`, and there is no CLI equivalent. The tool
  returns app metadata, deployment history, and a dashboard URL instead.
- **Sandbox and Function-call tools are slower** than the rest, for the reason
  above.
- **Secret values are never returned.** `list_secrets` reports names only.
