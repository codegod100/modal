# Tools

<!-- tools:count --> tools: <!-- tools:read-count --> that only read state, and
<!-- tools:write-count --> that change state or spend compute. These tables are
generated from `READ_TOOLS` and `WRITE_TOOLS` in
[`mcp/modal_mcp/server.py`](https://github.com/codegod100/modal/blob/main/mcp/modal_mcp/server.py).

Live apps can be addressed by deployed name (`my-app`) or app ID (`ap-...`).
Environment-aware tools accept `environment`; without it, hosted calls resolve
the caller's workspace default and local stdio honors the local Modal
configuration.

## Read tools

These are the only tools registered when `MODAL_MCP_READ_ONLY=1`.

<!-- tools:read -->

## Write tools

<!-- tools:write -->

`get_function_call_result` keeps its earlier conservative classification as a
write tool.

## SDK operations by area

| Area | Tools | Public SDK operations |
| --- | --- | --- |
| Workspace | `whoami`, `list_environments`, `get_workspace_costs` | `Workspace.from_context`, `Environment.objects.list`, `Workspace.billing.summary` |
| Apps | `list_apps`, `get_app`, `get_app_logs` | `Environment.apps.list`, `App.lookup`, `App.info`, `App.logs.fetch` |
| Functions | `get_function_stats`, `call_function`, `spawn_function`, `get_function_call_result`, `cancel_function_call` | `Function.from_name`, stats/remote/spawn, `FunctionCall.from_id`, get/cancel |
| HTTP services | `deploy_service` | `App.server`, `App.deploy`, `Server.from_name`, `Server.get_url` |
| Command functions | `deploy_command_function` | `App.function`, `App.deploy` |
| Web functions | `deploy_web_function` | `App.function`, `web_server`, `App.deploy`, `Function.from_name`, `Function.get_web_url` |
| Sandboxes | `create_sandbox`, `sandbox_exec`, `list_sandboxes`, `terminate_sandbox` | `Sandbox.create`, exec/list/from_id/terminate |
| Storage | `list_volumes`, `list_volume_files`, `read_volume_file`, `list_secrets`, `create_secret`, `list_dicts`, `list_queues` | Object managers, `Volume.iterdir`/read_file, `Secret.update` |
