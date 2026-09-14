"""Tool implementations for the Modal MCP server.

Each tool shells out to the local `modal` CLI, which is already installed and
authenticated, rather than talking to Modal's API directly. Commands that
support `--json` are parsed; the rest return trimmed text.

A few operations have no `modal` subcommand at all -- sandboxes, calling
deployed Functions, and reading an App's Function layout. Those are expressed as
generated Python run through `modal run`; see `_script.py`.
"""

import json
import shlex
from typing import Any, Literal

from ._cli import env_args, run, run_json
from ._script import py, run_script


def _parse_payload(raw: str | None, label: str) -> Any:
    if raw is None or raw.strip() == "":
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} must be valid JSON: {exc}") from exc


def _clip(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[-limit:], True


# --------------------------------------------------------------------------
# workspace
# --------------------------------------------------------------------------


async def whoami() -> dict:
    """Identify the workspace and profile the CLI is currently using."""
    _, profile, _ = await run("profile", "current")
    environments = await run_json("environment", "list")
    default_env = next(
        (e["name"] for e in environments if str(e.get("active")).lower() == "true"),
        None,
    )
    workspace = profile.strip()
    return {
        "workspace": workspace,
        "default_environment": default_env,
        "environments": [e["name"] for e in environments],
        "dashboard_url": f"https://modal.com/apps/{workspace}",
    }


async def list_environments() -> list[dict]:
    """List environments in the workspace."""
    return await run_json("environment", "list")


async def get_workspace_costs(cycle: str | None = None) -> dict:
    """Cost summary for one monthly billing cycle.

    `cycle` accepts "this month" (the default), "last month", or an ISO month
    like "2026-08". Modal aligns billing summaries to month boundaries.
    """
    args = ["billing", "summary"]
    if cycle:
        args += ["--for", cycle]
    return {"cycle": cycle or "this month", "summary": await run_json(*args)}


# --------------------------------------------------------------------------
# apps
# --------------------------------------------------------------------------


async def list_apps(
    environment: str | None = None,
    state: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """List apps that are running, deployed, or recently stopped."""
    apps = await run_json("app", "list", *env_args(environment))
    if state:
        apps = [a for a in apps if a.get("state") == state]
    return apps[:limit]


async def get_app(app: str, environment: str | None = None) -> dict:
    """Metadata and deployment history for one app.

    Note: this does not list the app's Functions. Modal exposes no way to read a
    deployed App's layout -- `App.registered_functions` is documented as not
    working for an App retrieved via lookup, and there is no CLI equivalent. Use
    the dashboard URL below to see them.
    """
    apps = await run_json("app", "list", *env_args(environment))
    match = next(
        (a for a in apps if a.get("app_id") == app or a.get("description") == app),
        None,
    )
    if match is None:
        known = ", ".join(sorted(a.get("description") or "" for a in apps)) or "(none)"
        raise ValueError(f"No app {app!r}. Known apps: {known}")

    history = await run_json(
        "app", "history", match["app_id"], *env_args(environment)
    )
    _, workspace, _ = await run("profile", "current")
    return {
        **match,
        "history": history,
        "dashboard_url": (
            f"https://modal.com/apps/{workspace.strip()}/{match['app_id']}"
        ),
    }


async def get_app_logs(
    app: str,
    minutes: int = 30,
    search: str | None = None,
    stream: Literal["all", "stdout", "stderr"] = "all",
    limit: int = 200,
    environment: str | None = None,
) -> dict:
    """Fetch recent logs for an app. Returns the newest `limit` lines."""
    if minutes < 1 or minutes > 50400:
        raise ValueError("minutes must be between 1 and 50400 (35 days)")
    args = [
        "app",
        "logs",
        app,
        "--since",
        f"{minutes}m",
        "--tail",
        str(limit),
        "--timestamps",
        *env_args(environment),
    ]
    if search:
        args += ["--search", search]
    if stream != "all":
        args += ["--source", stream]
    _, stdout, _ = await run(*args, timeout=180)
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    return {
        "app": app,
        "window_minutes": minutes,
        "line_count": len(lines),
        "lines": lines[-limit:],
    }


async def get_deployment_history(app: str, environment: str | None = None) -> dict:
    """Version history for a deployed app."""
    history = await run_json("app", "history", app, *env_args(environment))
    return {"app": app, "history": history}


async def stop_app(app: str, environment: str | None = None) -> dict:
    """Stop a running or deployed app. This tears down its containers."""
    _, stdout, _ = await run("app", "stop", app, "--yes", *env_args(environment))
    return {"app": app, "stopped": True, "output": stdout.strip()}


# --------------------------------------------------------------------------
# functions
# --------------------------------------------------------------------------


async def get_function_stats(
    app: str, function: str, environment: str | None = None
) -> dict:
    """Live queue depth and container counts for a deployed function."""
    return await run_script(
        f"""
fn = modal.Function.from_name({py(app)}, {py(function)}, environment_name={py(environment or None)})
stats = fn.get_current_stats()
_emit({{
    "app": {py(app)},
    "function": {py(function)},
    "function_id": fn.object_id,
    "backlog": stats.backlog,
    "running_inputs": stats.num_running_inputs,
    "total_containers": stats.num_total_runners,
}})
"""
    )


def _call_args(args: str | None, kwargs: str | None) -> tuple[list, dict]:
    pos = _parse_payload(args, "args") or []
    kw = _parse_payload(kwargs, "kwargs") or {}
    if not isinstance(pos, list):
        raise ValueError("args must be a JSON array")
    if not isinstance(kw, dict):
        raise ValueError("kwargs must be a JSON object")
    return pos, kw


async def call_function(
    app: str,
    function: str,
    args: str | None = None,
    kwargs: str | None = None,
    timeout_seconds: int = 240,
    environment: str | None = None,
) -> dict:
    """Call a deployed Modal function and wait for its result."""
    pos, kw = _call_args(args, kwargs)
    return await run_script(
        f"""
fn = modal.Function.from_name({py(app)}, {py(function)}, environment_name={py(environment or None)})
result = fn.remote(*{py(pos)}, **{py(kw)})
_emit({{"app": {py(app)}, "function": {py(function)}, "status": "done", "result": result}})
""",
        timeout=timeout_seconds,
    )


async def spawn_function(
    app: str,
    function: str,
    args: str | None = None,
    kwargs: str | None = None,
    environment: str | None = None,
) -> dict:
    """Start a deployed function without waiting; returns a function_call_id."""
    pos, kw = _call_args(args, kwargs)
    return await run_script(
        f"""
fn = modal.Function.from_name({py(app)}, {py(function)}, environment_name={py(environment or None)})
call = fn.spawn(*{py(pos)}, **{py(kw)})
_emit({{"function_call_id": call.object_id, "hint": "Poll with get_function_call_result."}})
"""
    )


async def get_function_call_result(
    function_call_id: str, timeout_seconds: int = 0
) -> dict:
    """Fetch the result of a spawned call. timeout_seconds=0 returns immediately."""
    return await run_script(
        f"""
call = modal.FunctionCall.from_id({py(function_call_id)})
try:
    result = call.get(timeout={int(timeout_seconds)})
except TimeoutError:
    _emit({{"function_call_id": {py(function_call_id)}, "status": "pending"}})
else:
    _emit({{"function_call_id": {py(function_call_id)}, "status": "done", "result": result}})
""",
        timeout=max(240, int(timeout_seconds) + 120),
    )


async def cancel_function_call(
    function_call_id: str, terminate_containers: bool = False
) -> dict:
    """Cancel an in-flight function call."""
    return await run_script(
        f"""
call = modal.FunctionCall.from_id({py(function_call_id)})
call.cancel(terminate_containers={py(bool(terminate_containers))})
_emit({{"function_call_id": {py(function_call_id)}, "cancelled": True}})
"""
    )


# --------------------------------------------------------------------------
# containers
# --------------------------------------------------------------------------


async def list_containers(
    app: str | None = None, environment: str | None = None
) -> list[dict]:
    """List running containers, optionally narrowed to one app."""
    args = ["container", "list", *env_args(environment)]
    if app:
        args += ["--app-id", app]
    return await run_json(*args)


async def stop_container(container_id: str) -> dict:
    """Stop a single running container."""
    _, stdout, _ = await run("container", "stop", container_id)
    return {"container_id": container_id, "stopped": True, "output": stdout.strip()}


# --------------------------------------------------------------------------
# sandboxes
# --------------------------------------------------------------------------

_SANDBOX_APP = "modal-mcp-sandboxes"


async def create_sandbox(
    image: str = "python:3.12-slim",
    command: str | None = None,
    timeout_seconds: int = 600,
    idle_timeout_seconds: int | None = 300,
    cpu: float | None = None,
    memory_mb: int | None = None,
    gpu: str | None = None,
    environment: str | None = None,
) -> dict:
    """Start a sandbox VM from a public registry image. Run commands with sandbox_exec.

    The sandbox bills until it stops, so it terminates after `timeout_seconds` at
    the latest, and after `idle_timeout_seconds` with no command running.
    """
    cmd = f"*{py(['sh', '-c', command])}," if command else ""
    return await run_script(
        f"""
sb_app = modal.App.lookup({py(_SANDBOX_APP)}, create_if_missing=True, environment_name={py(environment or None)})
sb = modal.Sandbox.create(
    {cmd}
    app=sb_app,
    image=modal.Image.from_registry({py(image)}),
    timeout={int(timeout_seconds)},
    idle_timeout={py(idle_timeout_seconds)},
    cpu={py(cpu)},
    memory={py(memory_mb)},
    gpu={py(gpu)},
)
_emit({{
    "sandbox_id": sb.object_id,
    "app": {py(_SANDBOX_APP)},
    "image": {py(image)},
    "timeout_seconds": {int(timeout_seconds)},
    "idle_timeout_seconds": {py(idle_timeout_seconds)},
    "hint": "Use sandbox_exec to run commands, terminate_sandbox when done.",
}})
"""
    )


async def sandbox_exec(
    sandbox_id: str,
    command: str,
    workdir: str | None = None,
    timeout_seconds: int = 120,
    max_output_chars: int = 20000,
) -> dict:
    """Run a shell command inside a running sandbox and return its output."""
    result = await run_script(
        f"""
sb = modal.Sandbox.from_id({py(sandbox_id)})
p = sb.exec("sh", "-c", {py(command)}, workdir={py(workdir)}, timeout={int(timeout_seconds)})
out = p.stdout.read()
err = p.stderr.read()
code = p.wait()
_emit({{"exit_code": code, "stdout": out, "stderr": err}})
""",
        timeout=timeout_seconds + 180,
    )
    stdout, c1 = _clip(result.get("stdout", ""), max_output_chars)
    stderr, c2 = _clip(result.get("stderr", ""), max_output_chars)
    return {
        "sandbox_id": sandbox_id,
        "command": command,
        "exit_code": result.get("exit_code"),
        "stdout": stdout,
        "stderr": stderr,
        "truncated": c1 or c2,
    }


async def list_sandboxes(
    app: str | None = None, environment: str | None = None, limit: int = 50
) -> list[dict]:
    """List sandboxes in the workspace."""
    result = await run_script(
        f"""
items = []
for sb in modal.Sandbox.list(app_id={py(app)}):
    items.append({{"sandbox_id": sb.object_id}})
    if len(items) >= {int(limit)}:
        break
_emit({{"sandboxes": items}})
"""
    )
    return result.get("sandboxes", [])


async def terminate_sandbox(sandbox_id: str) -> dict:
    """Terminate a running sandbox."""
    return await run_script(
        f"""
modal.Sandbox.from_id({py(sandbox_id)}).terminate()
_emit({{"sandbox_id": {py(sandbox_id)}, "terminated": True}})
"""
    )


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------


async def list_volumes(environment: str | None = None) -> list[dict]:
    """List volumes in an environment."""
    return await run_json("volume", "list", *env_args(environment))


async def list_volume_files(
    volume: str,
    path: str = "/",
    limit: int = 200,
    environment: str | None = None,
) -> dict:
    """List files and directories inside a volume."""
    entries = await run_json("volume", "ls", volume, path, *env_args(environment))
    return {
        "volume": volume,
        "path": path,
        "truncated": len(entries) > limit,
        "entries": entries[:limit],
    }


async def read_volume_file(
    volume: str,
    path: str,
    max_bytes: int = 100_000,
    environment: str | None = None,
) -> dict:
    """Read a file stored in a volume, truncated to max_bytes."""
    _, stdout, _ = await run(
        "volume", "get", volume, path, "-", *env_args(environment), timeout=180
    )
    content, truncated = _clip(stdout, max_bytes)
    return {
        "volume": volume,
        "path": path,
        "bytes_read": len(content),
        "truncated": truncated,
        "content": content,
    }


async def list_secrets(environment: str | None = None) -> list[dict]:
    """List secret names. Secret values are never returned."""
    return await run_json("secret", "list", *env_args(environment))


async def create_secret(
    name: str,
    entries: str,
    overwrite: bool = False,
    environment: str | None = None,
) -> dict:
    """Create a secret from a JSON object of key/value pairs."""
    data = _parse_payload(entries, "entries")
    if not isinstance(data, dict) or not data:
        raise ValueError("entries must be a non-empty JSON object of string key/values")
    pairs = [f"{k}={v}" for k, v in data.items()]
    args = ["secret", "create", name, *pairs, *env_args(environment)]
    if overwrite:
        args.append("--force")
    await run(*args)
    return {"name": name, "keys": sorted(data), "created": True}


async def list_dicts(environment: str | None = None) -> list[dict]:
    """List Modal Dicts in an environment."""
    return await run_json("dict", "list", *env_args(environment))


async def list_queues(environment: str | None = None) -> list[dict]:
    """List Modal Queues in an environment with their current size."""
    return await run_json("queue", "list", *env_args(environment))


async def modal_cli(args: str, timeout_seconds: int = 120) -> dict:
    """Escape hatch: run any `modal` command not covered by a tool above.

    Pass arguments as you would type them, without the leading `modal`, e.g.
    "app list --json" or "volume ls my-vol /data". Run "--help" to discover
    commands; Modal adds features regularly.
    """
    argv = shlex.split(args)
    if not argv:
        raise ValueError("args must not be empty")
    code, stdout, stderr = await run(*argv, timeout=timeout_seconds, check=False)
    return {
        "command": f"modal {args}",
        "exit_code": code,
        "stdout": stdout.strip(),
        "stderr": stderr.strip(),
    }
