from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import modal
import pytest

from modal_mcp import _service, tools


async def test_typed_deployment_uses_sdk_caller_client_and_proxy_auth(sdk, monkeypatch):
    definition = NS(app_id="ap-service", deploy=NS(aio=AsyncMock()))
    build = Mock(return_value=definition)
    monkeypatch.setattr(tools, "service_app", build)
    server = NS(
        object_id="fu-service", get_url=NS(aio=AsyncMock(return_value="https://service.modal.run"))
    )
    lookup = Mock(return_value=server)
    monkeypatch.setattr(modal.Server, "from_name", lookup)
    result = await tools.deploy_service(
        "example",
        "public/image",
        ["server", "--port", "8080"],
        8080,
        environment="dev",
        cpu=2,
        memory_mb=1024,
        min_containers=1,
        max_containers=2,
    )
    build.assert_called_once_with(
        "example", "public/image", ["server", "--port", "8080"], 8080, 2, 1024, 1, 2, 60, None
    )
    definition.deploy.aio.assert_awaited_once_with(environment_name="dev", client=sdk.client)
    lookup.assert_called_once_with("example", "service", environment_name="dev", client=sdk.client)
    assert result == {
        "app": "example",
        "app_id": "ap-service",
        "environment": "dev",
        "server_id": "fu-service",
        "url": "https://service.modal.run",
        "requires_proxy_auth": True,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"argv": []},
        {"argv": "server"},
        {"argv": [1]},
        {"argv": ["bad\x00arg"]},
        {"port": 0},
        {"port": 65536},
        {"cpu": 0},
        {"memory_mb": 0},
        {"min_containers": 2, "max_containers": 1},
        {"max_containers": 0},
        {"startup_timeout_seconds": 0},
        {"app": ""},
        {"image": ""},
        {"gpu": " "},
    ],
)
async def test_invalid_service_request_fails_before_sdk(sdk, changes):
    params = {"app": "example", "image": "public/image", "argv": ["server"], "port": 8080}
    params.update(changes)
    with pytest.raises(ValueError):
        await tools.deploy_service(**params)
    sdk.factory.assert_not_called()


def test_service_definition_uses_only_typed_serialized_remote_hooks(monkeypatch):
    captured = {}
    app = NS()

    def register(**kwargs):
        captured["options"] = kwargs

        def decorator(cls):
            captured["class"] = cls
            return cls

        return decorator

    app.server = register
    monkeypatch.setattr(modal, "App", Mock(return_value=app))
    image = object()
    registry = Mock(return_value=image)
    monkeypatch.setattr(modal.Image, "from_registry", registry)
    monkeypatch.setattr(modal, "enter", lambda: lambda fn: fn)
    monkeypatch.setattr(modal, "exit", lambda: lambda fn: fn)
    # Even definition construction must not execute the argv locally.
    assert (
        _service.service_app(
            "example", "public/image", ["server", "--port", "8080"], 8080, 1, 512, 0, 1, 60
        )
        is app
    )
    registry.assert_called_once_with("public/image", add_python="3.12")
    assert captured["options"] == {
        "image": image,
        "name": "service",
        "port": 8080,
        "cpu": 1,
        "memory": 512,
        "gpu": None,
        "min_containers": 0,
        "max_containers": 1,
        "startup_timeout": 60,
        "serialized": True,
        "include_source": False,
        "unauthenticated": False,
    }
    import subprocess

    process = NS(terminate=Mock())
    popen = Mock(return_value=process)
    monkeypatch.setattr(subprocess, "Popen", popen)
    remote = captured["class"]()
    remote.start()
    popen.assert_called_once_with(["server", "--port", "8080"])
    remote.stop()
    process.terminate.assert_called_once()


def test_real_sdk_accepts_service_definition_without_network():
    app = _service.service_app(
        "offline-example",
        "python:3.12-slim",
        ["python", "-m", "http.server", "8080"],
        8080,
        1,
        512,
        0,
        1,
        60,
    )
    assert app.name == "offline-example" and app.app_id is None


async def test_web_function_deployment_returns_public_url(sdk, monkeypatch):
    definition = NS(app_id="ap-web", deploy=NS(aio=AsyncMock()))
    build = Mock(return_value=definition)
    monkeypatch.setattr(tools, "web_function_app", build)
    function = NS(
        object_id="fu-web", get_web_url=NS(aio=AsyncMock(return_value="https://web.modal.run"))
    )
    lookup = Mock(return_value=function)
    monkeypatch.setattr(modal.Function, "from_name", lookup)
    result = await tools.deploy_web_function(
        "site",
        "python:3.12-slim",
        ["python", "-m", "http.server", "8000"],
        8000,
        public=True,
        add_python=None,
        environment="dev",
    )
    build.assert_called_once_with(
        "site",
        "python:3.12-slim",
        ["python", "-m", "http.server", "8000"],
        8000,
        1.0,
        512,
        0,
        1,
        60,
        True,
        None,
        None,
    )
    definition.deploy.aio.assert_awaited_once_with(environment_name="dev", client=sdk.client)
    lookup.assert_called_once_with("site", "web", environment_name="dev", client=sdk.client)
    assert result == {
        "app": "site",
        "app_id": "ap-web",
        "environment": "dev",
        "function_id": "fu-web",
        "url": "https://web.modal.run",
        "requires_proxy_auth": False,
    }


async def test_web_function_defaults_to_proxy_auth(sdk, monkeypatch):
    definition = NS(app_id="ap-web", deploy=NS(aio=AsyncMock()))
    build = Mock(return_value=definition)
    monkeypatch.setattr(tools, "web_function_app", build)
    function = NS(object_id="fu-web", get_web_url=NS(aio=AsyncMock(return_value="https://x")))
    monkeypatch.setattr(modal.Function, "from_name", Mock(return_value=function))
    result = await tools.deploy_web_function("site", "img", ["server"], 8000)
    assert build.call_args.args[-3:] == (False, "3.12", None)
    assert result["requires_proxy_auth"] is True


@pytest.mark.parametrize("deploy", ["deploy_service", "deploy_web_function"])
async def test_gpu_is_passed_to_definition(sdk, monkeypatch, deploy):
    definition = NS(app_id="ap-gpu", deploy=NS(aio=AsyncMock()))
    builder = "service_app" if deploy == "deploy_service" else "web_function_app"
    build = Mock(return_value=definition)
    monkeypatch.setattr(tools, builder, build)
    url = NS(aio=AsyncMock(return_value="https://gpu.modal.run"))
    handle = NS(object_id="fu-gpu", get_url=url, get_web_url=url)
    monkeypatch.setattr(modal.Server, "from_name", Mock(return_value=handle))
    monkeypatch.setattr(modal.Function, "from_name", Mock(return_value=handle))
    await getattr(tools, deploy)("gpu-app", "img", ["server"], 8000, gpu="L4")
    assert build.call_args.args[-1] == "L4"


def test_real_sdk_accepts_gpu_definitions_without_network():
    service = _service.service_app(
        "gpu-service", "python:3.12-slim", ["server"], 8080, 1, 512, 0, 1, 60, "L4"
    )
    web = _service.web_function_app(
        "gpu-web", "python:3.12-slim", ["server"], 8080, 1, 512, 0, 1, 60, False, None, "H100:2"
    )
    assert service.app_id is None and web.app_id is None


async def test_invalid_web_function_request_fails_before_sdk(sdk):
    with pytest.raises(ValueError):
        await tools.deploy_web_function("site", "img", [], 8000)
    sdk.factory.assert_not_called()


@pytest.mark.parametrize("public", [True, False])
def test_web_function_definition_runs_argv_only_remotely(monkeypatch, public):
    captured = {}
    app = NS()

    def register(**kwargs):
        captured["options"] = kwargs
        return lambda fn: fn

    def web_server(port, **kwargs):
        captured["web"] = (port, kwargs)

        def decorator(fn):
            captured["fn"] = fn
            return fn

        return decorator

    app.function = register
    monkeypatch.setattr(modal, "App", Mock(return_value=app))
    image = object()
    registry = Mock(return_value=image)
    monkeypatch.setattr(modal.Image, "from_registry", registry)
    monkeypatch.setattr(modal, "web_server", web_server)
    import subprocess

    popen = Mock()
    monkeypatch.setattr(subprocess, "Popen", popen)
    assert (
        _service.web_function_app(
            "site", "img", ["server", "--port", "8000"], 8000, 1, 512, 0, 1, 60, public, None
        )
        is app
    )
    popen.assert_not_called()
    registry.assert_called_once_with("img", add_python=None)
    assert captured["options"] == {
        "image": image,
        "name": "web",
        "cpu": 1,
        "memory": 512,
        "gpu": None,
        "min_containers": 0,
        "max_containers": 1,
        "serialized": True,
        "include_source": False,
    }
    assert captured["web"] == (8000, {"startup_timeout": 60, "requires_proxy_auth": not public})
    captured["fn"]()
    popen.assert_called_once_with(["server", "--port", "8000"])


def test_real_sdk_accepts_web_function_definition_without_network():
    app = _service.web_function_app(
        "offline-site",
        "python:3.12-slim",
        ["python", "-m", "http.server", "8000"],
        8000,
        1,
        512,
        0,
        1,
        60,
        True,
        None,
    )
    assert app.name == "offline-site" and app.app_id is None


async def test_command_function_deploys_with_gpu_and_setup(sdk, monkeypatch):
    definition = NS(app_id="ap-cmd", deploy=NS(aio=AsyncMock()))
    build = Mock(return_value=definition)
    monkeypatch.setattr(tools, "command_function_app", build)
    result = await tools.deploy_command_function(
        "job", "python:3.12-slim", ["make"], gpu="L4", add_python=None, environment="dev"
    )
    build.assert_called_once_with(
        "job", "python:3.12-slim", ["make"], 1.0, 512, "L4", 0, 1, 600, None
    )
    definition.deploy.aio.assert_awaited_once_with(environment_name="dev", client=sdk.client)
    assert result["app_id"] == "ap-cmd" and result["function"] == "run"


@pytest.mark.parametrize(
    "changes",
    [
        {"setup_argv": []},
        {"setup_argv": [""]},
        {"gpu": ""},
        {"timeout_seconds": 0},
        {"cpu": 0},
        {"image": ""},
    ],
)
async def test_invalid_command_function_request_fails_before_sdk(sdk, changes):
    params = {"app": "job", "image": "img"}
    params.update(changes)
    with pytest.raises(ValueError):
        await tools.deploy_command_function(**params)
    sdk.factory.assert_not_called()


def test_command_function_runs_setup_once_and_returns_output(monkeypatch, tmp_path):
    captured = {}
    app = NS()

    def register(**kwargs):
        captured["options"] = kwargs

        def decorator(fn):
            captured["fn"] = fn
            return fn

        return decorator

    app.function = register
    monkeypatch.setattr(modal, "App", Mock(return_value=app))
    monkeypatch.setattr(modal.Image, "from_registry", Mock(return_value="image"))
    import os
    import subprocess

    real_exists = os.path.exists
    marker = tmp_path / "marker"
    monkeypatch.setattr(
        os.path,
        "exists",
        lambda p: real_exists(marker) if p == "/tmp/.modal-mcp-setup-done" else real_exists(p),
    )
    real_open = open

    def fake_open(p, *a, **k):
        return real_open(marker if p == "/tmp/.modal-mcp-setup-done" else p, *a, **k)

    monkeypatch.setattr("builtins.open", fake_open)
    run = Mock(return_value=NS(returncode=0, stdout="hi", stderr=""))
    monkeypatch.setattr(subprocess, "run", run)
    _service.command_function_app("job", "img", ["setup"], 1, 512, "L4", 0, 1, 600, None)
    assert captured["options"]["gpu"] == "L4" and captured["options"]["name"] == "run"
    run.assert_not_called()  # nothing executes at definition time
    out = tmp_path / "out.bin"
    out.write_bytes(b"\x01\x02")
    first = captured["fn"](["work"], output_file=str(out))
    second = captured["fn"](["work"])
    assert [c.args[0] for c in run.call_args_list] == [["setup"], ["work"], ["work"]]
    assert first["output_base64"] == "AQI=" and first["stdout"] == "hi"
    assert "output_base64" not in second


def test_real_sdk_accepts_command_function_without_network():
    app = _service.command_function_app(
        "offline-job", "python:3.12-slim", None, 1, 512, "L4", 0, 1, 600, None
    )
    assert app.name == "offline-job" and app.app_id is None
