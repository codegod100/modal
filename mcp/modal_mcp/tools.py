"""Modal MCP operations implemented with the public Python SDK.

Every API handle is bound to the request's explicit client. There is no local
command execution, generated Python, private tool RPC, or CLI escape hatch.
"""

import asyncio
import json
from collections import deque
from collections.abc import AsyncGenerator
from contextlib import aclosing
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, cast

import modal
from modal.exception import TimeoutError as ModalTimeoutError

from ._sdk import client_session, environment_name, json_value, require_write
from ._service import service_app


def _positive(value: int, label: str, maximum: int = 10000) -> None:
    if not 1 <= value <= maximum:
        raise ValueError(f"{label} must be between 1 and {maximum}")


def _parse_payload(raw: str | None, label: str) -> Any:
    if raw is None or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} must be valid JSON: {exc}") from exc


def _call_args(args: str | None, kwargs: str | None) -> tuple[list, dict]:
    pos = _parse_payload(args, "args")
    kw = _parse_payload(kwargs, "kwargs")
    pos = [] if pos is None else pos
    kw = {} if kw is None else kw
    if not isinstance(pos, list):
        raise TypeError("args must be a JSON array")
    if not isinstance(kw, dict):
        raise TypeError("kwargs must be a JSON object")
    return pos, kw


async def _apps(client, environment: str):
    env = modal.Environment.from_name(environment, client=client)
    return await env.apps.list.aio()


async def _app(client, name_or_id: str, environment: str):
    if not name_or_id.startswith("ap-"):
        return await modal.App.lookup.aio(name_or_id, environment_name=environment, client=client)
    for app in await _apps(client, environment):
        if app.app_id == name_or_id:
            return app
    raise ValueError(f"No live app {name_or_id!r} in environment {environment!r}.")


async def whoami() -> dict:
    """Identify the caller's workspace and default environment."""
    async with client_session() as client:
        workspace = modal.Workspace.from_context(client=client)
        await workspace.hydrate.aio()
        default = await environment_name(client, None)
        environments = await modal.Environment.objects.list.aio(client=client)
        return {
            "workspace": workspace.name,
            "default_environment": default,
            "environments": [env.name for env in environments],
            "dashboard_url": f"https://modal.com/apps/{workspace.name}",
        }


async def list_environments() -> list[dict]:
    """List environments in the authenticated caller's workspace."""
    async with client_session() as client:
        default = await environment_name(client, None)
        items = await modal.Environment.objects.list.aio(client=client)
        return [{"name": item.name, "active": item.name == default} for item in items]


async def get_workspace_costs(cycle: str | None = None) -> dict:
    """Workspace billing summary: this month, last month, or YYYY-MM."""
    async with client_session() as client:
        workspace = modal.Workspace.from_context(client=client)
        summary = await workspace.billing.summary.aio(cycle)
        return {"cycle": cycle or "this month", "summary": json_value(summary)}


async def list_apps(
    environment: str | None = None, state: str | None = None, limit: int = 100
) -> list[dict]:
    """List live apps; the public SDK excludes stopped and disabled apps."""
    _positive(limit, "limit")
    async with client_session() as client:
        env = await environment_name(client, environment)
        items = []
        for app in await _apps(client, env):
            info = json_value(await app.info.aio())
            info["state"] = info["lifecycle"]["state"]
            if state is None or info["state"] == state:
                items.append(info)
                if len(items) >= limit:
                    break
        return items


async def get_app(app: str, environment: str | None = None) -> dict:
    """Live app metadata, current lifecycle, functions, and server IDs.

    Full historical deployment versions are not exposed by the public SDK.
    """
    async with client_session() as client:
        env = await environment_name(client, environment)
        handle = await _app(client, app, env)
        return {
            **json_value(await handle.info.aio()),
            "dashboard_url": await handle.get_dashboard_url.aio(),
        }


async def get_app_logs(
    app: str,
    minutes: int = 30,
    search: str | None = None,
    stream: Literal["all", "stdout", "stderr"] = "all",
    limit: int = 200,
    environment: str | None = None,
) -> dict:
    """Fetch the newest limit log entries within a UTC time window."""
    _positive(minutes, "minutes", 50400)
    _positive(limit, "limit")
    if stream not in ("all", "stdout", "stderr"):
        raise ValueError("stream must be all, stdout, or stderr")
    async with client_session() as client:
        env = await environment_name(client, environment)
        handle = await _app(client, app, env)
        entries: deque = deque(maxlen=limit)

        async def collect():
            async with aclosing(
                handle.logs.fetch.aio(
                    since=datetime.now(timezone.utc) - timedelta(minutes=minutes),
                    source=None if stream == "all" else stream,
                    search_text=search or "",
                )
            ) as logs:
                async for entry in logs:
                    entries.append(entry)

        await asyncio.wait_for(collect(), timeout=180)
        return {
            "app": app,
            "window_minutes": minutes,
            "line_count": len(entries),
            "lines": [entry.message for entry in entries],
            "entries": json_value(list(entries)),
        }


async def get_function_stats(app: str, function: str, environment: str | None = None) -> dict:
    """Live queue depth and container counts for a deployed function."""
    async with client_session() as client:
        env = await environment_name(client, environment)
        fn = modal.Function.from_name(app, function, environment_name=env, client=client)
        stats = await fn.get_current_stats.aio()
        return {
            "app": app,
            "function": function,
            "function_id": fn.object_id,
            "backlog": stats.backlog,
            "running_inputs": stats.num_running_inputs,
            "total_containers": stats.num_total_runners,
        }


async def call_function(
    app: str,
    function: str,
    args: str | None = None,
    kwargs: str | None = None,
    timeout_seconds: int = 240,
    environment: str | None = None,
) -> dict:
    """Call a deployed function through the SDK and wait for its result.

    Timing out stops waiting; it does not cancel remote execution.
    """
    require_write()
    _positive(timeout_seconds, "timeout_seconds", 86400)
    pos, kw = _call_args(args, kwargs)
    async with client_session() as client:
        env = await environment_name(client, environment)
        fn = modal.Function.from_name(app, function, environment_name=env, client=client)
        result = await asyncio.wait_for(fn.remote.aio(*pos, **kw), timeout_seconds)
        return {"app": app, "function": function, "status": "done", "result": json_value(result)}


async def spawn_function(
    app: str,
    function: str,
    args: str | None = None,
    kwargs: str | None = None,
    environment: str | None = None,
) -> dict:
    """Start a deployed function without waiting; returns its call ID."""
    require_write()
    pos, kw = _call_args(args, kwargs)
    async with client_session() as client:
        env = await environment_name(client, environment)
        fn = modal.Function.from_name(app, function, environment_name=env, client=client)
        call = await fn.spawn.aio(*pos, **kw)
        return {"function_call_id": call.object_id, "hint": "Poll with get_function_call_result."}


async def get_function_call_result(function_call_id: str, timeout_seconds: int = 0) -> dict:
    """Fetch a spawned call's result; zero timeout returns immediately."""
    require_write()  # Preserve the existing conservative read-only inventory.
    if not 0 <= timeout_seconds <= 86400:
        raise ValueError("timeout_seconds must be between 0 and 86400")
    async with client_session() as client:
        call = modal.FunctionCall.from_id(function_call_id, client=client)
        try:
            result = await call.get.aio(timeout=timeout_seconds)
        except (TimeoutError, ModalTimeoutError):
            return {"function_call_id": function_call_id, "status": "pending"}
        return {
            "function_call_id": function_call_id,
            "status": "done",
            "result": json_value(result),
        }


async def cancel_function_call(function_call_id: str, terminate_containers: bool = False) -> dict:
    """Cancel a function call through its caller-bound SDK handle."""
    require_write()
    async with client_session() as client:
        call = modal.FunctionCall.from_id(function_call_id, client=client)
        await call.cancel.aio(terminate_containers=terminate_containers)
        return {"function_call_id": function_call_id, "cancelled": True}


async def deploy_service(
    app: str,
    image: str,
    argv: list[str],
    port: int,
    environment: str | None = None,
    cpu: float = 1.0,
    memory_mb: int = 512,
    min_containers: int = 0,
    max_containers: int = 1,
    startup_timeout_seconds: int = 60,
) -> dict:
    """Create or update a named HTTP service via App.server and App.deploy.

    The public registry image must contain the service and its dependencies.
    argv runs only inside the service container, which must listen on
    0.0.0.0:port. The endpoint requires Modal proxy authentication. This tool
    accepts no Python source, host file path, or shell command string.
    Deploying the same app name replaces its previous definition.
    """
    require_write()
    if not app.strip() or not image.strip():
        raise ValueError("app and image must not be empty")
    if (
        not isinstance(argv, list)
        or not argv
        or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in argv)
    ):
        raise ValueError("argv must be a non-empty array of non-empty command arguments")
    _positive(port, "port", 65535)
    _positive(memory_mb, "memory_mb", 1_000_000)
    _positive(max_containers, "max_containers", 1000)
    _positive(startup_timeout_seconds, "startup_timeout_seconds", 3600)
    if cpu <= 0 or not 0 <= min_containers <= max_containers:
        raise ValueError("cpu must be positive and 0 <= min_containers <= max_containers")
    async with client_session() as client:
        env = await environment_name(client, environment)
        definition = service_app(
            app,
            image,
            argv,
            port,
            cpu,
            memory_mb,
            min_containers,
            max_containers,
            startup_timeout_seconds,
        )
        await definition.deploy.aio(environment_name=env, client=client)
        server = modal.Server.from_name(app, "service", environment_name=env, client=client)
        url = await server.get_url.aio()
        return {
            "app": app,
            "app_id": definition.app_id,
            "environment": env,
            "server_id": server.object_id,
            "url": url,
            "requires_proxy_auth": True,
        }


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
    """Create a sandbox via the SDK; optional command runs inside the sandbox."""
    require_write()
    _positive(timeout_seconds, "timeout_seconds", 86400)
    if idle_timeout_seconds is not None:
        _positive(idle_timeout_seconds, "idle_timeout_seconds", 86400)
    async with client_session() as client:
        env = await environment_name(client, environment)
        app = await modal.App.lookup.aio(
            _SANDBOX_APP, create_if_missing=True, environment_name=env, client=client
        )
        args = ("sh", "-c", command) if command else ()
        sandbox = await modal.Sandbox.create.aio(
            *args,
            app=app,
            client=client,
            image=modal.Image.from_registry(image),
            timeout=timeout_seconds,
            idle_timeout=idle_timeout_seconds,
            cpu=cpu,
            memory=memory_mb,
            gpu=gpu,
        )
        return {
            "sandbox_id": sandbox.object_id,
            "app": _SANDBOX_APP,
            "image": image,
            "timeout_seconds": timeout_seconds,
            "idle_timeout_seconds": idle_timeout_seconds,
            "hint": "Use sandbox_exec to run commands, terminate_sandbox when done.",
        }


async def sandbox_exec(
    sandbox_id: str,
    command: str,
    workdir: str | None = None,
    timeout_seconds: int = 120,
    max_output_chars: int = 20000,
) -> dict:
    """Run a command inside an existing sandbox through the SDK, not locally."""
    require_write()
    _positive(timeout_seconds, "timeout_seconds", 86400)
    _positive(max_output_chars, "max_output_chars", 1_000_000)
    async with client_session() as client:
        sb = await modal.Sandbox.from_id.aio(sandbox_id, client=client)
        process = await sb.exec.aio("sh", "-c", command, workdir=workdir, timeout=timeout_seconds)
        stdout, stderr, code = await asyncio.gather(
            process.stdout.read.aio(), process.stderr.read.aio(), process.wait.aio()
        )
        return {
            "sandbox_id": sandbox_id,
            "command": command,
            "exit_code": code,
            "stdout": stdout[-max_output_chars:],
            "stderr": stderr[-max_output_chars:],
            "truncated": len(stdout) > max_output_chars or len(stderr) > max_output_chars,
        }


async def list_sandboxes(
    app: str | None = None, environment: str | None = None, limit: int = 50
) -> list[dict]:
    """List sandboxes belonging to live apps in one environment."""
    _positive(limit, "limit")
    async with client_session() as client:
        env = await environment_name(client, environment)
        apps = [await _app(client, app, env)] if app else await _apps(client, env)
        items = []
        for handle in apps:
            async with aclosing(
                modal.Sandbox.list.aio(app_id=handle.app_id, client=client)
            ) as sandboxes:
                async for sb in sandboxes:
                    items.append({"sandbox_id": sb.object_id, "app_id": handle.app_id})
                    if len(items) >= limit:
                        return items
        return items


async def terminate_sandbox(sandbox_id: str) -> dict:
    """Terminate a sandbox through the SDK."""
    require_write()
    async with client_session() as client:
        sb = await modal.Sandbox.from_id.aio(sandbox_id, client=client)
        await sb.terminate.aio()
        return {"sandbox_id": sandbox_id, "terminated": True}


async def _storage_list(kind, id_key: str, environment: str | None, limit: int):
    _positive(limit, "limit")
    async with client_session() as client:
        env = await environment_name(client, environment)
        handles = await kind.objects.list.aio(
            max_objects=limit, environment_name=env, client=client
        )
        return [
            {id_key: handle.object_id, **json_value(await handle.info.aio())} for handle in handles
        ]


async def list_volumes(environment: str | None = None, limit: int = 100) -> list[dict]:
    """List named volumes and their SDK metadata."""
    return await _storage_list(modal.Volume, "volume_id", environment, limit)


async def list_volume_files(
    volume: str, path: str = "/", limit: int = 200, environment: str | None = None
) -> dict:
    """List immediate children inside a volume, limited to limit entries."""
    _positive(limit, "limit")
    async with client_session() as client:
        env = await environment_name(client, environment)
        handle = modal.Volume.from_name(volume, environment_name=env, client=client)
        entries = []
        files = cast(AsyncGenerator[Any, None], handle.iterdir.aio(path, recursive=False))
        async with aclosing(files) as files:
            async for entry in files:
                entries.append(json_value(entry))
                if len(entries) > limit:
                    break
        return {
            "volume": volume,
            "path": path,
            "truncated": len(entries) > limit,
            "entries": entries[:limit],
        }


async def read_volume_file(
    volume: str, path: str, max_bytes: int = 100_000, environment: str | None = None
) -> dict:
    """Read at most max_bytes from the beginning of a file as UTF-8 text."""
    _positive(max_bytes, "max_bytes", 1_000_000)
    async with client_session() as client:
        env = await environment_name(client, environment)
        handle = modal.Volume.from_name(volume, environment_name=env, client=client)
        data = bytearray()
        truncated = False
        async with aclosing(handle.read_file.aio(path)) as chunks:
            async for chunk in chunks:
                remaining = max_bytes - len(data)
                data.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    truncated = True
                    break
        return {
            "volume": volume,
            "path": path,
            "bytes_read": len(data),
            "truncated": truncated,
            "encoding": "utf-8",
            "content": data.decode("utf-8", errors="replace"),
        }


async def list_secrets(environment: str | None = None, limit: int = 100) -> list[dict]:
    """List secret names and metadata; never secret values."""
    return await _storage_list(modal.Secret, "secret_id", environment, limit)


async def create_secret(
    name: str, entries: str, overwrite: bool = False, environment: str | None = None
) -> dict:
    """Create a named secret. With overwrite, merge and overwrite supplied keys.

    Existing keys not supplied in entries are preserved by Secret.update.
    """
    require_write()
    data = _parse_payload(entries, "entries")
    if (
        not isinstance(data, dict)
        or not data
        or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in data.items()
        )
    ):
        raise ValueError("entries must be a non-empty JSON object of string keys and values")
    async with client_session() as client:
        env = await environment_name(client, environment)
        await modal.Secret.objects.create.aio(
            name, data, allow_existing=overwrite, environment_name=env, client=client
        )
        if overwrite:
            secret = modal.Secret.from_name(name, environment_name=env, client=client)
            await secret.update.aio(data)
        return {"name": name, "keys": sorted(data), "created": True, "merge_existing": overwrite}


async def list_dicts(environment: str | None = None, limit: int = 100) -> list[dict]:
    """List named dictionaries and their SDK metadata."""
    return await _storage_list(modal.Dict, "dict_id", environment, limit)


async def list_queues(environment: str | None = None, limit: int = 100) -> list[dict]:
    """List named queues and their SDK metadata (without queue contents)."""
    return await _storage_list(modal.Queue, "queue_id", environment, limit)
