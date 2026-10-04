# modal-mcp

An MCP server for [Modal](https://modal.com) using the public Python SDK directly.
MCP requests do not invoke the Modal CLI, launch local subprocesses, generate
Python scripts, or expose an arbitrary command escape hatch.

Requires Modal **1.6 or newer** and FastMCP 4. Local stdio uses the existing Modal
profile; the hosted server binds each request to that caller's Modal credentials.

## Run locally

```bash
cd mcp
pip install -e .
modal token new  # if you do not already have a Modal profile
claude mcp add modal -- /path/to/mcp/.venv/bin/modal-mcp
```

The vendor CLI above is only for developer setup. Tool operations use SDK clients
constructed explicitly from the selected profile's credentials.

## Host on Modal

```bash
cd mcp
pip install -e .
modal deploy app.py
```

Deploy with a local `modal` client of 1.6 or newer. Modal runs the deploying
client's version inside the container regardless of the image's `pip_install`,
and the app tools need 1.6 APIs; `app.py` refuses to deploy from an older client.

Connect your MCP client to `https://<workspace>--modal-mcp.modal.run/mcp` and sign
in with Modal. Each caller's tools use their own credentials, never the host
container's identity. No profile or credential environment variables are mutated.
Missing or malformed hosted credentials fail closed. A fresh client is closed
when each operation finishes, including failed operations.

The existing browser login uses Modal's internal `TokenFlowCreate` and
`TokenFlowWait` device-grant RPCs. These are **not a supported public SDK login
API** and remain a compatibility dependency of hosted login. This refactor adds
no internal RPCs to tools; it preserves that existing login flow. Migrating login
to Modal-issued OAuth client credentials is separate work requiring client setup.

Sign-ins are saved to the `modal-mcp-auth-state` Volume and restored when a new
container starts, so callers stay signed in across scaledown and redeploys. That
volume holds callers' Modal tokens; anyone who can read it can act as them.
`max_containers=1` keeps requests on the same process, since pending logins and
the live token tables are held in memory. `MODAL_MCP_ALLOWED_WORKSPACES` can
restrict login.

## Tools

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

Environment-aware tools accept `environment`. Without it, hosted calls resolve the
caller's workspace default; local stdio first honors the local Modal environment
configuration. Every handle and API call is bound to the caller's explicit client.

`MODAL_MCP_READ_ONLY=1` (also `true` or `yes`) registers only the **14 read tools**.
All write implementations also enforce this policy when called directly. Function
result polling retains its previous conservative classification as a write tool.

## Deploy an HTTP service through the API

Call `deploy_service` with structured arguments, for example:

```json
{
  "app": "example-http",
  "image": "python:3.12-slim",
  "argv": ["python", "-m", "http.server", "8080", "--bind", "0.0.0.0"],
  "port": 8080,
  "environment": "main",
  "cpu": 1.0,
  "memory_mb": 512,
  "min_containers": 0,
  "max_containers": 1,
  "startup_timeout_seconds": 60,
  "gpu": null
}
```

This creates or updates the named app. Reusing an app name replaces its previous
app definition. The service and its dependencies must be in the public registry
image; Python 3.12 is added for Modal's runtime. The argv command starts only in
the service container, through a serialized `@modal.enter()` hook. It must bind
`0.0.0.0` on the declared port. The endpoint requires Modal proxy authentication;
the tool returns its URL and does not create or expose proxy credentials.

`gpu` attaches a GPU to every container, using Modal's GPU type names such as
`"T4"`, `"L4"`, `"A100"`, `"H100"` or `"H100:2"` for several. Leave it `null`
(the default) for CPU-only. Modal validates the name when the app deploys. The
image must carry its own GPU userland (CUDA libraries, frameworks), since Modal
provides only the driver.

The tool does not accept Python source, import caller modules, upload local source,
or run a shell command on the MCP host. Private registry credentials and arbitrary
source-based app deployment are outside this tool's scope.

## Volumes, secrets and build steps

`deploy_service`, `deploy_web_function` and `deploy_command_function` also take:

- `volumes`: `{"/data": "my-volume"}` mounts a Volume at an absolute path,
  creating it if missing. Use `max_containers: 1` for services that need a
  single owner of their data directory.
- `secrets`: `["my-secret"]` exposes each Secret's keys as environment
  variables. Create them with `create_secret`.
- `image_commands`: shell commands run on Modal while building the image, after
  the registry image (and `add_python`). Use them to install or compile a service
  that has no registry image. They never run on the MCP host.

`create_sandbox` takes `volumes` and `secrets` too. With a Secret holding
`MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET`, a sandbox can run `modal deploy`, which
is how this server can redeploy itself from `main`.

Names resolve in the environment the app deploys to. For example, an S3 store:

```json
{
  "app": "minio",
  "image": "debian:bookworm-slim",
  "image_commands": [
    "apt-get update && apt-get install -y curl git ca-certificates",
    "curl -fsSL https://go.dev/dl/go1.24.8.linux-amd64.tar.gz | tar -C /usr/local -xz && git clone -q --depth 1 -b RELEASE.2025-10-15T17-29-55Z https://github.com/minio/minio /src && cd /src && CGO_ENABLED=0 /usr/local/go/bin/go build -trimpath -o /usr/local/bin/minio . && rm -rf /src /usr/local/go /root/go /root/.cache"
  ],
  "argv": ["minio", "server", "/data", "--address", "0.0.0.0:9000"],
  "port": 9000,
  "public": true,
  "volumes": {"/data": "minio-data"},
  "secrets": ["minio-root"],
  "max_containers": 1
}
```

## Deploy a public website through the API

`deploy_web_function` takes the same inputs as `deploy_service` but deploys a
`@modal.web_server` function named `web`. Pass `"public": true` to serve the
endpoint without Modal proxy authentication (the default keeps proxy auth on),
which is what a website needs. Anyone signed in to this MCP can therefore
publish public endpoints in their workspace.

`add_python` defaults to `"3.12"`. Set it to `null` for images that already
ship Python, such as `python:3.12-slim`, where adding a second one fails the
image build. `gpu` works the same way as for `deploy_service`.

## Deploy a GPU or batch command function

`deploy_command_function` deploys a function named `run` with no HTTP endpoint,
so no proxy token is involved. Call it with `call_function` (or
`spawn_function`), which uses the caller's own Modal credentials:

```json
{"app": "my-job", "function": "run",
 "kwargs": "{\"argv\": [\"/app/render\", \"out.png\"], \"output_file\": \"out.png\"}"}
```

`setup_argv` runs once per container before its first call, for installs and
builds. Each call returns `returncode`, `stdout`, `stderr` and, when
`output_file` exists after a successful run, its bytes as `output_base64`.
`gpu`, `cpu`, `memory_mb` and `timeout_seconds` (setup plus one call) size the
container. Like `sandbox_exec`, this lets any signed-in caller run commands in
containers in their own workspace; nothing runs on the MCP host.

## API limits and changes

- The public SDK lists **live apps**, excluding stopped and disabled apps. App
  metadata now includes current lifecycle, functions, and server IDs, but no full
  deployment history. `get_deployment_history` was removed.
- `stop_app`, `list_containers`, and `stop_container` were removed because the
  documented public SDK has no corresponding management methods. Function stats
  still report container counts. Use Modal's dashboard for those management tasks.
- `modal_cli` and the CLI/script adapters were removed entirely.
- `list_sandboxes` filters by live apps in the resolved environment. `sandbox_exec`
  still runs the explicitly requested command inside a remote sandbox via its SDK.
- Storage listings return SDK metadata and default to 100 named objects. Secret
  values are never returned. `create_secret(overwrite=true)` merges supplied keys
  through `Secret.update`; existing unspecified keys remain intact.
- Volume reads return the first `max_bytes` bytes, decoded as UTF-8 with replacement
  for invalid bytes. Limits count bytes, not characters. Billing decimals are
  returned as strings to retain precision.
- A `call_function` timeout stops waiting and does not cancel the remote call.

## Offline verification

```bash
pip install -e '.[dev]'
pytest -q
ruff check .
ruff format --check .
mypy modal_mcp
```

Tests mock SDK calls and assert that no network or local process execution occurs.
They cover the MCP inventory, typed service deployment, SDK call arguments,
read-only enforcement, per-caller client binding, missing hosted credentials, and
OAuth credential propagation. They do not deploy or mutate live Modal resources.
