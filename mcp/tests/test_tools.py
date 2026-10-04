from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import modal
import pytest
from modal.types import AppInfo, AppLifecycle, AppState, LogEntry, SecretInfo

from modal_mcp import tools


def method(value=None, *, side_effect=None):
    return NS(aio=AsyncMock(return_value=value, side_effect=side_effect))


def stream(items, *, closed=None):
    async def values(*args, **kwargs):
        try:
            for item in items:
                yield item
        finally:
            if closed is not None:
                closed.append(True)

    return NS(aio=Mock(side_effect=values))


def app_info(app_id="ap-live", state=AppState.DEPLOYED):
    return AppInfo(
        app_id,
        "live-service",
        AppLifecycle(
            state,
            1,
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            "caller",
            None,
            None,
            None,
            None,
        ),
        {"work": "fu-work"},
        {"service": "fu-service"},
    )


def app_handle(app_id="ap-live", state=AppState.DEPLOYED):
    return NS(
        app_id=app_id,
        info=method(app_info(app_id, state)),
        get_dashboard_url=method("https://modal.com/apps/caller/" + app_id),
    )


async def test_workspace_identity_environments_and_billing(sdk, monkeypatch):
    environments = method([NS(name="caller-default"), NS(name="dev")])
    monkeypatch.setattr(type(modal.Environment.objects), "list", environments)
    result = await tools.whoami()
    assert result["workspace"] == "caller"
    assert result["default_environment"] == "caller-default"
    sdk.workspace.hydrate.aio.assert_awaited_once()
    environments.aio.assert_awaited_once_with(client=sdk.client)
    assert await tools.list_environments() == [
        {"name": "caller-default", "active": True},
        {"name": "dev", "active": False},
    ]
    sdk.workspace.billing.summary.aio.return_value = {"billed_cost": Decimal("1.2300")}
    assert await tools.get_workspace_costs("last month") == {
        "cycle": "last month",
        "summary": {"billed_cost": "1.2300"},
    }
    sdk.workspace.billing.summary.aio.assert_awaited_once_with("last month")
    for call in sdk.workspace_lookup.call_args_list:
        assert call.kwargs["client"] is sdk.client


async def test_app_listing_filters_limits_and_binds_client(sdk, monkeypatch):
    env = NS(apps=NS(list=method([app_handle(), app_handle("ap-detached", AppState.DETACHED)])))
    from_name = Mock(return_value=env)
    monkeypatch.setattr(modal.Environment, "from_name", from_name)
    result = await tools.list_apps("dev", state="deployed", limit=1)
    assert len(result) == 1 and result[0]["app_id"] == "ap-live"
    assert result[0]["functions"] == {"work": "fu-work"}
    assert result[0]["state"] == "deployed"
    from_name.assert_called_once_with("dev", client=sdk.client)


async def test_get_app_by_name_and_live_id(sdk, monkeypatch):
    handle = app_handle()
    lookup = method(handle)
    monkeypatch.setattr(modal.App, "lookup", lookup)
    result = await tools.get_app("live-service", "dev")
    assert result["app_id"] == "ap-live" and "history" not in result
    lookup.aio.assert_awaited_once_with("live-service", environment_name="dev", client=sdk.client)
    monkeypatch.setattr(
        modal.Environment, "from_name", Mock(return_value=NS(apps=NS(list=method([handle]))))
    )
    assert (await tools.get_app("ap-live", "dev"))["app_id"] == "ap-live"
    with pytest.raises(ValueError, match="No live app"):
        await tools.get_app("ap-missing", "dev")


async def test_logs_use_public_fetch_filters_and_keep_newest_entries(sdk, monkeypatch):
    handle = app_handle()
    now = datetime.now(timezone.utc)
    handle.logs = NS(
        fetch=stream([LogEntry(str(i), now, "stdout", "ap-live", []) for i in range(3)])
    )
    monkeypatch.setattr(modal.App, "lookup", method(handle))
    result = await tools.get_app_logs(
        "service", minutes=5, search="needle", stream="stdout", limit=2, environment="dev"
    )
    assert result["lines"] == ["1", "2"]
    call = handle.logs.fetch.aio.call_args.kwargs
    assert call["source"] == "stdout" and call["search_text"] == "needle"
    assert call["since"].tzinfo == timezone.utc


async def test_function_stats_calls_spawn_and_binding(sdk, monkeypatch):
    fn = NS(
        object_id="fu-work",
        get_current_stats=method(NS(backlog=2, num_running_inputs=3, num_total_runners=4)),
        remote=method({"answer": 42}),
        spawn=method(NS(object_id="fc-call")),
    )
    lookup = Mock(return_value=fn)
    monkeypatch.setattr(modal.Function, "from_name", lookup)
    stats = await tools.get_function_stats("service", "work", "dev")
    assert stats["backlog"] == 2 and stats["total_containers"] == 4
    assert (await tools.call_function("service", "work", "[1]", '{"x":2}', environment="dev"))[
        "result"
    ] == {"answer": 42}
    fn.remote.aio.assert_awaited_once_with(1, x=2)
    assert (await tools.spawn_function("service", "work", environment="dev"))[
        "function_call_id"
    ] == "fc-call"
    for call in lookup.call_args_list:
        assert call.kwargs == {"environment_name": "dev", "client": sdk.client}


@pytest.mark.parametrize(
    "args,kwargs",
    [("false", None), ("0", None), ("{}", None), (None, "[]"), (None, "false"), ("{", None)],
)
async def test_invalid_function_payloads_do_not_open_a_client(sdk, args, kwargs):
    with pytest.raises((ValueError, TypeError)):
        await tools.call_function("app", "fn", args, kwargs)
    sdk.factory.assert_not_called()


async def test_call_result_polling_and_cancellation(sdk, monkeypatch):
    call = NS(get=method(side_effect=TimeoutError()), cancel=method())
    lookup = Mock(return_value=call)
    monkeypatch.setattr(modal.FunctionCall, "from_id", lookup)
    assert (await tools.get_function_call_result("fc-call"))["status"] == "pending"
    call.get.aio.side_effect = None
    call.get.aio.return_value = "finished"
    assert (await tools.get_function_call_result("fc-call", 5))["result"] == "finished"
    assert (await tools.cancel_function_call("fc-call", True))["cancelled"]
    call.cancel.aio.assert_awaited_once_with(terminate_containers=True)
    for item in lookup.call_args_list:
        assert item.kwargs["client"] is sdk.client


async def test_sdk_sandbox_creation_and_remote_exec(sdk, monkeypatch):
    app = app_handle()
    lookup = method(app)
    monkeypatch.setattr(modal.App, "lookup", lookup)
    create = method(NS(object_id="sb-sandbox"))
    monkeypatch.setattr(modal.Sandbox, "create", create)
    image_lookup = Mock(return_value=object())
    monkeypatch.setattr(modal.Image, "from_registry", image_lookup)
    result = await tools.create_sandbox(
        command="echo hello", cpu=2, memory_mb=1024, environment="dev"
    )
    assert result["sandbox_id"] == "sb-sandbox"
    lookup.aio.assert_awaited_once_with(
        "modal-mcp-sandboxes", create_if_missing=True, environment_name="dev", client=sdk.client
    )
    assert create.aio.call_args.args == ("sh", "-c", "echo hello")
    assert create.aio.call_args.kwargs["client"] is sdk.client
    assert create.aio.call_args.kwargs["app"] is app
    assert create.aio.call_args.kwargs["memory"] == 1024
    assert create.aio.call_args.kwargs["volumes"] == {}
    assert create.aio.call_args.kwargs["secrets"] == []
    process = NS(stdout=NS(read=method("abcdef")), stderr=NS(read=method("err")), wait=method(7))
    sandbox = NS(exec=method(process), terminate=method())
    attach = method(sandbox)
    monkeypatch.setattr(modal.Sandbox, "from_id", attach)
    result = await tools.sandbox_exec("sb-sandbox", "echo test", workdir="/tmp", max_output_chars=3)
    assert result["stdout"] == "def" and result["exit_code"] == 7 and result["truncated"]
    sandbox.exec.aio.assert_awaited_once_with("sh", "-c", "echo test", workdir="/tmp", timeout=120)
    await tools.terminate_sandbox("sb-sandbox")
    sandbox.terminate.aio.assert_awaited_once()
    for call in attach.aio.call_args_list:
        assert call.kwargs["client"] is sdk.client


async def test_sandbox_listing_scopes_environment_and_closes_at_limit(sdk, monkeypatch):
    handle = app_handle()
    from_name = Mock(return_value=NS(apps=NS(list=method([handle]))))
    monkeypatch.setattr(modal.Environment, "from_name", from_name)
    closed = []
    listing = stream([NS(object_id="sb-one"), NS(object_id="sb-two")], closed=closed)
    monkeypatch.setattr(modal.Sandbox, "list", listing)
    assert await tools.list_sandboxes(environment="dev", limit=1) == [
        {"sandbox_id": "sb-one", "app_id": "ap-live"}
    ]
    from_name.assert_called_once_with("dev", client=sdk.client)
    listing.aio.assert_called_once_with(app_id="ap-live", client=sdk.client)
    assert closed == [True]


@pytest.mark.parametrize(
    "kind,fn,id_key",
    [
        (modal.Volume, tools.list_volumes, "volume_id"),
        (modal.Secret, tools.list_secrets, "secret_id"),
        (modal.Dict, tools.list_dicts, "dict_id"),
        (modal.Queue, tools.list_queues, "queue_id"),
    ],
)
async def test_storage_manager_lists_are_client_bound_metadata_only(
    sdk, monkeypatch, kind, fn, id_key
):
    info = SecretInfo("example", "dev", datetime(2026, 1, 1, tzinfo=timezone.utc), "caller")
    listing = method([NS(object_id="object-id", info=method(info))])
    monkeypatch.setattr(type(kind.objects), "list", listing)
    result = await fn("dev", limit=10)
    assert result[0][id_key] == "object-id" and result[0]["name"] == "example"
    assert set(result[0]) == {id_key, "name", "environment_name", "created_at", "created_by"}
    listing.aio.assert_awaited_once_with(max_objects=10, environment_name="dev", client=sdk.client)


@pytest.mark.parametrize(
    "chunks,cap,expected,truncated",
    [
        ([b"abc", b"def"], 4, "abcd", True),
        ([b"abc"], 3, "abc", False),
        (["é".encode()], 1, "�", True),
        ([], 10, "", False),
    ],
)
async def test_volume_reads_count_bytes_from_start(
    sdk, monkeypatch, chunks, cap, expected, truncated
):
    closed = []
    handle = NS(read_file=stream(chunks, closed=closed))
    lookup = Mock(return_value=handle)
    monkeypatch.setattr(modal.Volume, "from_name", lookup)
    result = await tools.read_volume_file("files", "/file", max_bytes=cap, environment="dev")
    assert result["content"] == expected and result["truncated"] == truncated
    assert result["bytes_read"] <= cap
    lookup.assert_called_once_with("files", environment_name="dev", client=sdk.client)
    assert closed == [True]


async def test_volume_file_listing_bounds_iteration(sdk, monkeypatch):
    @dataclass
    class Entry:
        path: str

    handle = NS(iterdir=stream([Entry("one"), Entry("two"), Entry("three")]))
    monkeypatch.setattr(modal.Volume, "from_name", Mock(return_value=handle))
    result = await tools.list_volume_files("files", limit=2, environment="dev")
    assert result["entries"] == [{"path": "one"}, {"path": "two"}] and result["truncated"]
    handle.iterdir.aio.assert_called_once_with("/", recursive=False)


@pytest.mark.parametrize("overwrite", [False, True])
async def test_secret_creation_and_explicit_merge_do_not_return_values(sdk, monkeypatch, overwrite):
    create = method()
    monkeypatch.setattr(type(modal.Secret.objects), "create", create)
    secret = NS(update=method())
    lookup = Mock(return_value=secret)
    monkeypatch.setattr(modal.Secret, "from_name", lookup)
    result = await tools.create_secret(
        "example", '{"KEY":"dummy-value"}', overwrite, environment="dev"
    )
    create.aio.assert_awaited_once_with(
        "example",
        {"KEY": "dummy-value"},
        allow_existing=overwrite,
        environment_name="dev",
        client=sdk.client,
    )
    assert "dummy-value" not in str(result) and result["keys"] == ["KEY"]
    if overwrite:
        secret.update.aio.assert_awaited_once_with({"KEY": "dummy-value"})
        lookup.assert_called_once_with("example", environment_name="dev", client=sdk.client)
    else:
        lookup.assert_not_called()


@pytest.mark.parametrize("entries", ["{}", '{"key":1}', '{"key":null}', "[]", "invalid"])
async def test_invalid_secret_entries_fail_before_sdk(sdk, entries):
    with pytest.raises(ValueError):
        await tools.create_secret("name", entries)
    sdk.factory.assert_not_called()


async def test_sandbox_mounts_volumes_and_secrets_in_its_environment(sdk, monkeypatch):
    monkeypatch.setattr(modal.App, "lookup", method(app_handle()))
    create = method(NS(object_id="sb-x"))
    monkeypatch.setattr(modal.Sandbox, "create", create)
    monkeypatch.setattr(modal.Image, "from_registry", Mock(return_value=object()))
    volume, secret = object(), object()
    volume_lookup = Mock(return_value=volume)
    secret_lookup = Mock(return_value=secret)
    monkeypatch.setattr(modal.Volume, "from_name", volume_lookup)
    monkeypatch.setattr(modal.Secret, "from_name", secret_lookup)
    await tools.create_sandbox(volumes={"/data": "store"}, secrets=["token"], environment="dev")
    volume_lookup.assert_called_once_with(
        "store", create_if_missing=True, environment_name="dev", client=sdk.client
    )
    secret_lookup.assert_called_once_with("token", environment_name="dev", client=sdk.client)
    assert create.aio.call_args.kwargs["volumes"] == {"/data": volume}
    assert create.aio.call_args.kwargs["secrets"] == [secret]


async def test_invalid_sandbox_attachments_fail_before_sdk(sdk):
    with pytest.raises(ValueError):
        await tools.create_sandbox(volumes={"relative": "store"})
    sdk.factory.assert_not_called()
