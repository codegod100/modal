# Tools

24 tools: 14 that only read state, and 10 that change state or
spend compute. Tool lists come from `READ_TOOLS` and `WRITE_TOOLS` in
[`mcp/modal_mcp/server.py`](https://github.com/codegod100/modal/blob/main/mcp/modal_mcp/server.py).

Live apps can be addressed by deployed name (`my-app`) or app ID (`ap-...`).
Environment-aware tools accept `environment`; without it, hosted calls resolve
the caller's workspace default and local stdio honors the local Modal
configuration.

## Read tools

These are the only tools registered when `MODAL_MCP_READ_ONLY=1`.

| Tool | Parameters | What it does |
| --- | --- | --- |
| `whoami` | none | Identify the caller's workspace and default environment. |
| `list_environments` | none | List environments in the authenticated caller's workspace. |
| `get_workspace_costs` | `cycle` | Workspace billing summary: this month, last month, or YYYY-MM. |
| `list_apps` | `environment`, `state`, `limit` | List live apps; the public SDK excludes stopped and disabled apps. |
| `get_app` | `app`, `environment` | Live app metadata, current lifecycle, functions, and server IDs. |
| `get_app_logs` | `app`, `minutes`, `search`, `stream`, `limit`, `environment` | Fetch the newest limit log entries within a UTC time window. |
| `get_function_stats` | `app`, `function`, `environment` | Live queue depth and container counts for a deployed function. |
| `list_sandboxes` | `app`, `environment`, `limit` | List sandboxes belonging to live apps in one environment. |
| `list_volumes` | `environment`, `limit` | List named volumes and their SDK metadata. |
| `list_volume_files` | `volume`, `path`, `limit`, `environment` | List immediate children inside a volume, limited to limit entries. |
| `read_volume_file` | `volume`, `path`, `max_bytes`, `environment` | Read at most max_bytes from the beginning of a file as UTF-8 text. |
| `list_secrets` | `environment`, `limit` | List secret names and metadata; never secret values. |
| `list_dicts` | `environment`, `limit` | List named dictionaries and their SDK metadata. |
| `list_queues` | `environment`, `limit` | List named queues and their SDK metadata (without queue contents). |

## Write tools

| Tool | Parameters | What it does |
| --- | --- | --- |
| `call_function` | `app`, `function`, `args`, `kwargs`, `timeout_seconds`, `environment` | Call a deployed function through the SDK and wait for its result. |
| `spawn_function` | `app`, `function`, `args`, `kwargs`, `environment` | Start a deployed function without waiting; returns its call ID. |
| `get_function_call_result` | `function_call_id`, `timeout_seconds` | Fetch a spawned call's result; zero timeout returns immediately. |
| `cancel_function_call` | `function_call_id`, `terminate_containers` | Cancel a function call through its caller-bound SDK handle. |
| `create_sandbox` | `image`, `command`, `timeout_seconds`, `idle_timeout_seconds`, `cpu`, `memory_mb`, `gpu`, `environment` | Create a sandbox via the SDK; optional command runs inside the sandbox. |
| `sandbox_exec` | `sandbox_id`, `command`, `workdir`, `timeout_seconds`, `max_output_chars` | Run a command inside an existing sandbox through the SDK, not locally. |
| `terminate_sandbox` | `sandbox_id` | Terminate a sandbox through the SDK. |
| `create_secret` | `name`, `entries`, `overwrite`, `environment` | Create a named secret. With overwrite, merge and overwrite supplied keys. |
| `deploy_service` | `app`, `image`, `argv`, `port`, `environment`, `cpu`, `memory_mb`, `min_containers`, `max_containers`, `startup_timeout_seconds`, `gpu` | Create or update a named HTTP service via App.server and App.deploy. |
| `deploy_web_function` | `app`, `image`, `argv`, `port`, `public`, `add_python`, `environment`, `cpu`, `memory_mb`, `min_containers`, `max_containers`, `startup_timeout_seconds`, `gpu` | Create or update a named HTTP service as a Modal web function. |

`get_function_call_result` keeps its earlier conservative classification as a
write tool.

## SDK operations by area

| Area | Tools | Public SDK operations |
| --- | --- | --- |
| Workspace | `whoami`, `list_environments`, `get_workspace_costs` | `Workspace.from_context`, `Environment.objects.list`, `Workspace.billing.summary` |
| Apps | `list_apps`, `get_app`, `get_app_logs` | `Environment.apps.list`, `App.lookup`, `App.info`, `App.logs.fetch` |
| Functions | `get_function_stats`, `call_function`, `spawn_function`, `get_function_call_result`, `cancel_function_call` | `Function.from_name`, stats/remote/spawn, `FunctionCall.from_id`, get/cancel |
| HTTP services | `deploy_service` | `App.server`, `App.deploy`, `Server.from_name`, `Server.get_url` |
| Web functions | `deploy_web_function` | `App.function`, `web_server`, `App.deploy`, `Function.from_name`, `Function.get_web_url` |
| Sandboxes | `create_sandbox`, `sandbox_exec`, `list_sandboxes`, `terminate_sandbox` | `Sandbox.create`, exec/list/from_id/terminate |
| Storage | `list_volumes`, `list_volume_files`, `read_volume_file`, `list_secrets`, `create_secret`, `list_dicts`, `list_queues` | Object managers, `Volume.iterdir`/read_file, `Secret.update` |
