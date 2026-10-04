# Deploying services and jobs

Three write tools deploy a container image without uploading any Python
source: two as HTTP endpoints, one as a plain function you call through the SDK.

## `deploy_service`

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

All three deploy tools also take `volumes` (`{"/data": "my-volume"}`, created if
missing), `secrets` (Secret names whose keys become environment variables) and
`image_commands` (shell commands run on Modal while building the image, for a
service with no registry image). Names resolve in the environment the app deploys
to, and build commands never run on the MCP host. Keep `max_containers` at 1 for a
service that must be the single owner of a mounted data directory.

`create_sandbox` takes `volumes` and `secrets` too. With a Secret holding
`MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET`, a sandbox can run `modal deploy`, which
is how this server can redeploy itself from `main`.

## `deploy_web_function` for public websites

`deploy_web_function` takes the same inputs as `deploy_service` but deploys a
`@modal.web_server` function named `web`. Pass `"public": true` to serve the
endpoint without Modal proxy authentication (the default keeps proxy auth on),
which is what a website needs. Anyone signed in to this MCP can therefore
publish public endpoints in their workspace.

`add_python` defaults to `"3.12"`. Set it to `null` for images that already
ship Python, such as `python:3.12-slim`, where adding a second one fails the
image build. `gpu` works the same way as for `deploy_service`.

## `deploy_command_function` for GPU and batch jobs

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
