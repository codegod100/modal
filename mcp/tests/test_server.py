import ast
from pathlib import Path

import pytest
from fastmcp import Client

from modal_mcp import server, tools

READ = {
    "whoami",
    "list_environments",
    "get_workspace_costs",
    "list_apps",
    "get_app",
    "get_app_logs",
    "get_function_stats",
    "list_sandboxes",
    "list_volumes",
    "list_volume_files",
    "read_volume_file",
    "list_secrets",
    "list_dicts",
    "list_queues",
}
WRITE = {
    "call_function",
    "spawn_function",
    "get_function_call_result",
    "cancel_function_call",
    "terminate_sandbox",
    "create_secret",
    "deploy_service",
    "deploy_web_function",
    "deploy_command_function",
}


async def test_full_mcp_inventory_and_typed_deployment_schema():
    async with Client(server.build_mcp()) as client:
        listed = await client.list_tools()
    assert {tool.name for tool in listed} == READ | WRITE
    deployment = next(tool for tool in listed if tool.name == "deploy_service")
    schema = deployment.input_schema["properties"]
    assert schema["argv"]["type"] == "array"
    assert schema["argv"]["items"]["type"] == "string"
    assert schema["port"]["type"] == "integer"
    assert not {"source", "python", "file", "args", "unauthenticated"} & set(schema)


@pytest.mark.parametrize("setting", ["1", "true", "yes", "TRUE"])
async def test_read_only_inventory(monkeypatch, setting):
    monkeypatch.setenv("MODAL_MCP_READ_ONLY", setting)
    async with Client(server.build_mcp()) as client:
        assert {tool.name for tool in await client.list_tools()} == READ


@pytest.mark.parametrize(
    "name,args",
    [
        ("call_function", ("app", "fn")),
        ("spawn_function", ("app", "fn")),
        ("get_function_call_result", ("fc-id",)),
        ("cancel_function_call", ("fc-id",)),
        ("terminate_sandbox", ("sb-id",)),
        ("create_secret", ("name", '{"key":"value"}')),
        ("deploy_service", ("app", "image", ["server"], 8080)),
        ("deploy_web_function", ("app", "image", ["server"], 8080)),
        ("deploy_command_function", ("app", "image")),
    ],
)
async def test_write_implementations_fail_before_sdk_call(sdk, monkeypatch, name, args):
    monkeypatch.setenv("MODAL_MCP_READ_ONLY", "true")
    with pytest.raises(PermissionError, match="read-only"):
        await getattr(tools, name)(*args)
    sdk.factory.assert_not_called()


async def test_read_only_check_still_applies_after_registration(sdk, monkeypatch):
    mcp = server.build_mcp()
    monkeypatch.setenv("MODAL_MCP_READ_ONLY", "1")
    async with Client(mcp) as client:
        result = await client.call_tool("terminate_sandbox", {"sandbox_id": "sb-id"}, raise_on_error=False)
    assert result.is_error
    sdk.factory.assert_not_called()


def test_tools_contain_no_cli_private_rpc_or_local_execution():
    root = Path(tools.__file__).parent
    assert not (root / "_cli.py").exists()
    assert not (root / "_script.py").exists()
    for filename in ["tools.py", "_sdk.py", "server.py"]:
        tree = ast.parse((root / filename).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not {alias.name.split(".")[0] for alias in node.names} & {
                    "subprocess",
                    "modal_proto",
                }
            if isinstance(node, ast.ImportFrom):
                assert node.module not in {
                    "subprocess",
                    "modal.client",
                    "modal_proto",
                    "._cli",
                    "._script",
                }
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    assert node.func.id not in {"eval", "exec", "compile"}
                if isinstance(node.func, ast.Attribute):
                    assert node.func.attr not in {
                        "create_subprocess_exec",
                        "create_subprocess_shell",
                        "Popen",
                        "system",
                        "stub",
                    }
    assert not hasattr(tools, "modal_cli")


def test_state_store_follows_the_deployment_environment(monkeypatch, tmp_path):
    from modal_mcp import server
    from modal_mcp.auth import JsonFileStore

    monkeypatch.delenv("MODAL_MCP_STATE_DIR", raising=False)
    assert server._state_store() is None
    monkeypatch.setenv("MODAL_MCP_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("MODAL_MCP_STATE_VOLUME", raising=False)
    store = server._state_store()
    assert isinstance(store, JsonFileStore)
    assert store._path == tmp_path / "auth-state.json"


async def test_caller_log_names_client_and_tool_without_breaking_request(capsys):
    seen = {}

    async def inner(scope, receive, send):
        seen["body"] = (await receive())["body"]
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    app = server.CallerLog(inner)
    body = (
        b'[{"jsonrpc":"2.0","id":1,"method":"initialize","params":'
        b'{"clientInfo":{"name":"claude-ai","version":"1.0"}}},'
        b'{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"whoami","arguments":{"x":"secret"}}}]'
    )
    chunks = [{"type": "http.request", "body": body[:20], "more_body": True},
              {"type": "http.request", "body": body[20:]}]

    async def receive():
        return chunks.pop(0)

    sent = []

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/mcp",
             "headers": [(b"user-agent", b"Claude-User"), (b"authorization", b"Bearer t")]}
    await app(scope, receive, send)
    out = capsys.readouterr().out
    assert "client='claude-ai'" in out and "user_agent='Claude-User'" in out
    assert "tools/call: 'whoami'" in out
    assert "secret" not in out and "Bearer" not in out
    assert seen["body"] == body[:20]
    assert sent[-1]["body"] == b"ok"
