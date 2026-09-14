"""Tool implementations for the Modal MCP server.

Every tool returns plain JSON-serializable data. Protobuf responses are mapped
by hand rather than dumped wholesale so the output stays small and stable.
"""

import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from google.protobuf.empty_pb2 import Empty
from modal._logs import LogsFilters, fetch_logs
from modal.app import _App
from modal.client import _Client
from modal.exception import NotFoundError
from modal.functions import _Function, _FunctionCall
from modal.image import _Image
from modal.sandbox import _Sandbox
from modal.secret import _Secret
from modal.volume import FileEntryType, _Volume
from modal_proto import api_pb2

from ._client import env_or_default, get_client

APP_ID_RE = re.compile(r"^ap-[a-zA-Z0-9]{22}$")

APP_STATE_NAMES = {
    api_pb2.APP_STATE_DEPLOYED: "deployed",
    api_pb2.APP_STATE_DETACHED: "ephemeral (detached)",
    api_pb2.APP_STATE_DETACHED_DISCONNECTED: "ephemeral (detached)",
    api_pb2.APP_STATE_DISABLED: "disabled",
    api_pb2.APP_STATE_EPHEMERAL: "ephemeral",
    api_pb2.APP_STATE_INITIALIZING: "initializing",
    api_pb2.APP_STATE_STOPPED: "stopped",
    api_pb2.APP_STATE_STOPPING: "stopping",
}

FILE_ENTRY_TYPE_NAMES = {
    FileEntryType.FILE: "file",
    FileEntryType.DIRECTORY: "dir",
    FileEntryType.SYMLINK: "symlink",
}

FILE_DESCRIPTOR_NAMES = {
    api_pb2.FILE_DESCRIPTOR_STDOUT: "stdout",
    api_pb2.FILE_DESCRIPTOR_STDERR: "stderr",
    api_pb2.FILE_DESCRIPTOR_INFO: "info",
}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _ts(value: float | None) -> str | None:
    """Unix seconds -> ISO-8601 UTC, or None for the zero/unset value."""
    if not value:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def _parse_payload(raw: str | None, label: str) -> Any:
    if raw is None or raw.strip() == "":
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} must be valid JSON: {exc}") from exc


def _json_safe(value: Any, _depth: int = 0) -> Any:
    """Best-effort conversion of a function's return value into JSON."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if _depth > 6:
        return repr(value)
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v, _depth + 1) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, bytes):
        try:
            return value.decode()
        except UnicodeDecodeError:
            return f"<{len(value)} bytes>"
    return repr(value)


async def _resolve_app(
    client: _Client, app: str, environment: str | None
) -> tuple[str, str]:
    """Resolve an app ID or deployed-app name to (app_id, environment_name)."""
    if APP_ID_RE.match(app):
        await client.stub.AppGetLifecycle(api_pb2.AppGetLifecycleRequest(app_id=app))
        return app, env_or_default(environment)
    resp = await client.stub.AppGetByDeploymentName(
        api_pb2.AppGetByDeploymentNameRequest(
            name=app, environment_name=env_or_default(environment)
        )
    )
    app_id = resp.app_id or resp.previous_app_id
    if not app_id:
        raise NotFoundError(
            f"No app named {app!r} in environment {resp.environment_name!r}. "
            "Use list_apps to see what exists."
        )
    return app_id, resp.environment_name


async def _resolve_function_id(
    client: _Client, app: str, function: str, environment: str | None
) -> str:
    app_id, _ = await _resolve_app(client, app, environment)
    layout = await client.stub.AppGetLayout(api_pb2.AppGetLayoutRequest(app_id=app_id))
    ids = dict(layout.app_layout.function_ids)
    if function not in ids:
        available = ", ".join(sorted(ids)) or "(none)"
        raise NotFoundError(
            f"App {app!r} has no function {function!r}. Available: {available}"
        )
    return ids[function]


# --------------------------------------------------------------------------
# workspace
# --------------------------------------------------------------------------


async def whoami() -> dict:
    """Identify the workspace these credentials belong to."""
    client = await get_client()
    resp = await client.stub.WorkspaceNameLookup(Empty())
    envs = await client.stub.EnvironmentList(Empty())
    default_env = next((e.name for e in envs.items if e.default), None)
    return {
        "workspace_name": resp.workspace_name,
        "username": resp.username,
        "default_environment": default_env,
        "environments": [e.name for e in envs.items],
        "dashboard_url": f"https://modal.com/apps/{resp.workspace_name}",
    }


async def list_environments() -> list[dict]:
    """List environments in the workspace with their concurrency usage."""
    client = await get_client()
    resp = await client.stub.EnvironmentList(Empty())
    return [
        {
            "name": e.name,
            "default": e.default,
            "created_at": _ts(e.created_at),
            "webhook_suffix": e.webhook_suffix,
            "current_concurrent_tasks": e.current_concurrent_tasks,
            "max_concurrent_tasks": e.max_concurrent_tasks,
            "current_concurrent_gpus": e.current_concurrent_gpus,
            "max_concurrent_gpus": e.max_concurrent_gpus,
        }
        for e in resp.items
    ]


async def get_workspace_costs(days: int = 7) -> dict:
    """Metered and billed cost for the workspace over a recent window."""
    if days < 1 or days > 365:
        raise ValueError("days must be between 1 and 365")
    client = await get_client()
    start = int((datetime.now(tz=timezone.utc) - timedelta(days=days)).timestamp())
    resp = await client.stub.WorkspaceBillingSummary(
        api_pb2.WorkspaceBillingSummaryRequest(start_timestamp=start)
    )
    return {
        "start": _ts(resp.start_timestamp),
        "end": _ts(resp.end_timestamp),
        "metered_cost_usd": round(resp.metered_cost, 4),
        "billed_cost_usd": round(resp.billed_cost, 4),
        "breakdown_usd": {k: round(v, 4) for k, v in resp.metered_cost_breakdown.items()},
    }


# --------------------------------------------------------------------------
# apps
# --------------------------------------------------------------------------


async def list_apps(
    environment: str | None = None,
    state: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """List apps that are running, deployed, or recently stopped."""
    client = await get_client()
    resp = await client.stub.AppList(
        api_pb2.AppListRequest(environment_name=env_or_default(environment))
    )
    apps = []
    for a in resp.apps:
        state_name = APP_STATE_NAMES.get(a.state, "unknown")
        if state and state_name != state:
            continue
        apps.append(
            {
                "app_id": a.app_id,
                "name": a.name or a.description,
                "state": state_name,
                "running_tasks": a.n_running_tasks,
                "created_at": _ts(a.created_at),
                "stopped_at": _ts(a.stopped_at),
            }
        )
    return apps[:limit]


async def get_app(app: str, environment: str | None = None) -> dict:
    """Details for one app: state, functions, classes, and web endpoint URLs."""
    client = await get_client()
    app_id, env = await _resolve_app(client, app, environment)
    layout_resp = await client.stub.AppGetLayout(
        api_pb2.AppGetLayoutRequest(app_id=app_id)
    )
    layout = layout_resp.app_layout

    by_id = {o.object_id: o for o in layout.objects}
    functions = []
    for name, fid in sorted(layout.function_ids.items()):
        obj = by_id.get(fid)
        meta = obj.function_handle_metadata if obj else None
        functions.append(
            {
                "name": name,
                "function_id": fid,
                "web_url": (meta.web_url or None) if meta else None,
            }
        )
    classes = []
    for name, cid in sorted(layout.class_ids.items()):
        obj = by_id.get(cid)
        meta = obj.class_handle_metadata if obj else None
        classes.append(
            {
                "name": name,
                "class_id": cid,
                "methods": sorted(meta.methods.keys()) if meta else [],
            }
        )

    tasks = await client.stub.TaskList(api_pb2.TaskListRequest(app_id=app_id))
    return {
        "app_id": app_id,
        "environment": env,
        "functions": functions,
        "classes": classes,
        "running_containers": len(tasks.tasks),
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
    if minutes < 1 or minutes > 50400:  # server caps the range at 35 days
        raise ValueError("minutes must be between 1 and 50400 (35 days)")
    client = await get_client()
    app_id, _ = await _resolve_app(client, app, environment)

    source = {
        "all": api_pb2.FILE_DESCRIPTOR_UNSPECIFIED,
        "stdout": api_pb2.FILE_DESCRIPTOR_STDOUT,
        "stderr": api_pb2.FILE_DESCRIPTOR_STDERR,
    }[stream]
    filters = LogsFilters(source=source, search_text=search or "")

    until = datetime.now(tz=timezone.utc)
    since = until - timedelta(minutes=minutes)

    lines: list[dict] = []
    async for batch in fetch_logs(client, app_id, since, until, filters=filters):
        for item in batch.items:
            if not item.data:
                continue
            lines.append(
                {
                    "timestamp": _ts(item.timestamp),
                    "stream": FILE_DESCRIPTOR_NAMES.get(item.file_descriptor, "unknown"),
                    "container_id": item.container_id or None,
                    "text": item.data.rstrip("\n"),
                }
            )

    truncated = len(lines) > limit
    return {
        "app_id": app_id,
        "window_minutes": minutes,
        "total_matched": len(lines),
        "truncated": truncated,
        "lines": lines[-limit:],
    }


async def get_deployment_history(app: str, environment: str | None = None) -> dict:
    """Version history for a deployed app."""
    client = await get_client()
    app_id, env = await _resolve_app(client, app, environment)
    resp = await client.stub.AppDeploymentHistory(
        api_pb2.AppDeploymentHistoryRequest(app_id=app_id)
    )
    return {
        "app_id": app_id,
        "environment": env,
        "live_version": resp.production_app_version,
        "history": [
            {
                "version": h.version,
                "deployed_at": _ts(h.deployed_at),
                "deployed_by": h.deployed_by,
                "client_version": h.client_version,
                "tag": h.tag or None,
                "rollback_of_version": h.rollback_version or None,
            }
            for h in resp.app_deployment_histories
        ],
    }


async def stop_app(app: str, environment: str | None = None) -> dict:
    """Stop a running or deployed app. This tears down its containers."""
    client = await get_client()
    app_id, env = await _resolve_app(client, app, environment)
    await client.stub.AppStop(
        api_pb2.AppStopRequest(app_id=app_id, source=api_pb2.APP_STOP_SOURCE_PYTHON_CLIENT)
    )
    return {"app_id": app_id, "environment": env, "stopped": True}


# --------------------------------------------------------------------------
# functions
# --------------------------------------------------------------------------


async def get_function_stats(
    app: str, function: str, environment: str | None = None
) -> dict:
    """Live queue depth and container counts for a deployed function."""
    client = await get_client()
    function_id = await _resolve_function_id(client, app, function, environment)
    stats = await client.stub.FunctionGetCurrentStats(
        api_pb2.FunctionGetCurrentStatsRequest(function_id=function_id)
    )
    return {
        "app": app,
        "function": function,
        "function_id": function_id,
        "backlog": stats.backlog,
        "running_inputs": stats.num_running_inputs,
        "total_containers": stats.num_total_tasks,
    }


async def call_function(
    app: str,
    function: str,
    args: str | None = None,
    kwargs: str | None = None,
    environment: str | None = None,
) -> dict:
    """Call a deployed Modal function and wait for its result."""
    client = await get_client()
    pos = _parse_payload(args, "args") or []
    kw = _parse_payload(kwargs, "kwargs") or {}
    if not isinstance(pos, list):
        raise ValueError("args must be a JSON array")
    if not isinstance(kw, dict):
        raise ValueError("kwargs must be a JSON object")

    fn = _Function.from_name(
        app, function, environment_name=env_or_default(environment) or None
    )
    await fn.hydrate(client)
    started = time.monotonic()
    result = await fn.remote(*pos, **kw)
    return {
        "app": app,
        "function": function,
        "duration_seconds": round(time.monotonic() - started, 3),
        "result": _json_safe(result),
    }


async def spawn_function(
    app: str,
    function: str,
    args: str | None = None,
    kwargs: str | None = None,
    environment: str | None = None,
) -> dict:
    """Start a deployed function without waiting; returns a function_call_id."""
    client = await get_client()
    pos = _parse_payload(args, "args") or []
    kw = _parse_payload(kwargs, "kwargs") or {}
    if not isinstance(pos, list):
        raise ValueError("args must be a JSON array")
    if not isinstance(kw, dict):
        raise ValueError("kwargs must be a JSON object")

    fn = _Function.from_name(
        app, function, environment_name=env_or_default(environment) or None
    )
    await fn.hydrate(client)
    call = await fn.spawn(*pos, **kw)
    return {
        "function_call_id": call.object_id,
        "hint": "Poll with get_function_call_result.",
    }


async def get_function_call_result(
    function_call_id: str, timeout_seconds: float = 0
) -> dict:
    """Fetch the result of a spawned call. timeout_seconds=0 returns immediately."""
    client = await get_client()
    call = _FunctionCall._new_hydrated(function_call_id, client, None)
    try:
        result = await call.get(timeout=timeout_seconds)
    except TimeoutError:
        return {"function_call_id": function_call_id, "status": "pending"}
    return {
        "function_call_id": function_call_id,
        "status": "done",
        "result": _json_safe(result),
    }


async def cancel_function_call(
    function_call_id: str, terminate_containers: bool = False
) -> dict:
    """Cancel an in-flight function call."""
    client = await get_client()
    await client.stub.FunctionCallCancel(
        api_pb2.FunctionCallCancelRequest(
            function_call_id=function_call_id,
            terminate_containers=terminate_containers,
        )
    )
    return {"function_call_id": function_call_id, "cancelled": True}


# --------------------------------------------------------------------------
# containers
# --------------------------------------------------------------------------


async def list_containers(
    app: str | None = None, environment: str | None = None
) -> list[dict]:
    """List running containers, optionally narrowed to one app."""
    client = await get_client()
    app_id = ""
    if app:
        app_id, _ = await _resolve_app(client, app, environment)
    resp = await client.stub.TaskList(
        api_pb2.TaskListRequest(
            environment_name=env_or_default(environment), app_id=app_id
        )
    )
    return [
        {
            "container_id": t.task_id,
            "app_id": t.app_id,
            "app_description": t.app_description,
            "started_at": _ts(t.started_at),
            "enqueued_at": _ts(t.enqueued_at),
        }
        for t in resp.tasks
    ]


async def stop_container(container_id: str, graceful: bool = True) -> dict:
    """Stop a single running container."""
    client = await get_client()
    await client.stub.ContainerStop(
        api_pb2.ContainerStopRequest(task_id=container_id, graceful=graceful)
    )
    return {"container_id": container_id, "stopped": True, "graceful": graceful}


# --------------------------------------------------------------------------
# sandboxes
# --------------------------------------------------------------------------

_SANDBOX_APP = "modal-mcp-sandboxes"


async def create_sandbox(
    image: str = "python:3.12-slim",
    command: str | None = None,
    timeout_seconds: int = 600,
    cpu: float | None = None,
    memory_mb: int | None = None,
    gpu: str | None = None,
    environment: str | None = None,
) -> dict:
    """Start a sandbox VM from a public registry image. Run commands in it with sandbox_exec."""
    client = await get_client()
    env = env_or_default(environment) or None
    app = await _App.lookup(
        _SANDBOX_APP, client=client, environment_name=env, create_if_missing=True
    )
    img = _Image.from_registry(image)
    cmd = ("sh", "-c", command) if command else ()
    sb = await _Sandbox.create(
        *cmd,
        app=app,
        image=img,
        timeout=timeout_seconds,
        cpu=cpu,
        memory=memory_mb,
        gpu=gpu,
        client=client,
        environment_name=env,
    )
    return {
        "sandbox_id": sb.object_id,
        "app": _SANDBOX_APP,
        "image": image,
        "timeout_seconds": timeout_seconds,
        "hint": "Use sandbox_exec to run commands, terminate_sandbox when done.",
    }


async def sandbox_exec(
    sandbox_id: str,
    command: str,
    workdir: str | None = None,
    timeout_seconds: int = 120,
    max_output_chars: int = 20000,
) -> dict:
    """Run a shell command inside a running sandbox and return its output."""
    client = await get_client()
    sb = await _Sandbox.from_id(sandbox_id, client=client)
    proc = await sb.exec(
        "sh", "-c", command, workdir=workdir, timeout=timeout_seconds
    )
    stdout = await proc.stdout.read()
    stderr = await proc.stderr.read()
    returncode = await proc.wait()

    def clip(text: str) -> tuple[str, bool]:
        if len(text) <= max_output_chars:
            return text, False
        return text[-max_output_chars:], True

    out, out_clipped = clip(stdout)
    err, err_clipped = clip(stderr)
    return {
        "sandbox_id": sandbox_id,
        "command": command,
        "exit_code": returncode,
        "stdout": out,
        "stderr": err,
        "truncated": out_clipped or err_clipped,
    }


async def list_sandboxes(
    app: str | None = None,
    include_finished: bool = False,
    environment: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """List sandboxes in the workspace."""
    client = await get_client()
    app_id = ""
    if app:
        app_id, _ = await _resolve_app(client, app, environment)
    resp = await client.stub.SandboxList(
        api_pb2.SandboxListRequest(
            app_id=app_id,
            environment_name=env_or_default(environment),
            include_finished=include_finished,
        )
    )
    out = []
    for sb in list(resp.sandboxes)[:limit]:
        info = sb.task_info
        out.append(
            {
                "sandbox_id": sb.id,
                "app_id": sb.app_id,
                "name": sb.name or None,
                "created_at": _ts(sb.created_at),
                "started_at": _ts(info.started_at) if info else None,
                "finished_at": _ts(info.finished_at) if info else None,
                "timeout_seconds": sb.timeout_secs,
            }
        )
    return out


async def terminate_sandbox(sandbox_id: str) -> dict:
    """Terminate a running sandbox."""
    client = await get_client()
    sb = await _Sandbox.from_id(sandbox_id, client=client)
    await sb.terminate()
    return {"sandbox_id": sandbox_id, "terminated": True}


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------


async def list_volumes(environment: str | None = None) -> list[dict]:
    """List volumes in an environment."""
    client = await get_client()
    resp = await client.stub.VolumeList(
        api_pb2.VolumeListRequest(environment_name=env_or_default(environment))
    )
    return [
        {"name": v.label, "volume_id": v.volume_id, "created_at": _ts(v.created_at)}
        for v in resp.items
    ]


async def list_volume_files(
    volume: str,
    path: str = "/",
    recursive: bool = False,
    limit: int = 200,
    environment: str | None = None,
) -> dict:
    """List files and directories inside a volume."""
    client = await get_client()
    vol = await _Volume.from_name(
        volume, environment_name=env_or_default(environment) or None
    ).hydrate(client)
    entries = []
    truncated = False
    async for e in vol.iterdir(path, recursive=recursive):
        if len(entries) >= limit:
            truncated = True
            break
        entries.append(
            {
                "path": e.path,
                "type": FILE_ENTRY_TYPE_NAMES.get(e.type, "other"),
                "size": e.size,
                "mtime": _ts(e.mtime),
            }
        )
    return {"volume": volume, "path": path, "truncated": truncated, "entries": entries}


async def read_volume_file(
    volume: str,
    path: str,
    max_bytes: int = 100_000,
    environment: str | None = None,
) -> dict:
    """Read the beginning of a file stored in a volume."""
    client = await get_client()
    vol = await _Volume.from_name(
        volume, environment_name=env_or_default(environment) or None
    ).hydrate(client)
    chunks: list[bytes] = []
    size = 0
    truncated = False
    async for chunk in vol.read_file(path):
        chunks.append(chunk)
        size += len(chunk)
        if size >= max_bytes:
            truncated = True
            break
    data = b"".join(chunks)[:max_bytes]
    try:
        content = data.decode()
        binary = False
    except UnicodeDecodeError:
        content = repr(data[:1000])
        binary = True
    return {
        "volume": volume,
        "path": path,
        "bytes_read": len(data),
        "binary": binary,
        "truncated": truncated,
        "content": content,
    }


async def list_secrets(environment: str | None = None) -> list[dict]:
    """List secret names. Secret values are never returned."""
    client = await get_client()
    resp = await client.stub.SecretList(
        api_pb2.SecretListRequest(environment_name=env_or_default(environment))
    )
    return [
        {
            "name": s.label,
            "secret_id": s.secret_id,
            "environment": s.environment_name,
            "created_at": _ts(s.created_at),
            "last_used_at": _ts(s.last_used_at),
        }
        for s in resp.items
    ]


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
    env_dict = {str(k): str(v) for k, v in data.items()}

    client = await get_client()
    creation = (
        api_pb2.OBJECT_CREATION_TYPE_CREATE_OVERWRITE_IF_EXISTS
        if overwrite
        else api_pb2.OBJECT_CREATION_TYPE_CREATE_FAIL_IF_EXISTS
    )
    resp = await client.stub.SecretGetOrCreate(
        api_pb2.SecretGetOrCreateRequest(
            deployment_name=name,
            environment_name=env_or_default(environment),
            object_creation_type=creation,
            env_dict=env_dict,
        )
    )
    return {"name": name, "secret_id": resp.secret_id, "keys": sorted(env_dict)}


async def list_dicts(environment: str | None = None) -> list[dict]:
    """List Modal Dicts in an environment."""
    client = await get_client()
    resp = await client.stub.DictList(
        api_pb2.DictListRequest(environment_name=env_or_default(environment))
    )
    return [
        {"name": d.name, "dict_id": d.dict_id, "created_at": _ts(d.created_at)}
        for d in resp.dicts
    ]


async def list_queues(environment: str | None = None) -> list[dict]:
    """List Modal Queues in an environment with their current size."""
    client = await get_client()
    resp = await client.stub.QueueList(
        api_pb2.QueueListRequest(environment_name=env_or_default(environment))
    )
    return [
        {
            "name": q.name,
            "queue_id": q.queue_id,
            "total_size": q.total_size,
            "num_partitions": q.num_partitions,
            "created_at": _ts(q.created_at),
        }
        for q in resp.queues
    ]
