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
        "example", "public/image", ["server", "--port", "8080"], 8080, 2, 1024, 1, 2, 60
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
