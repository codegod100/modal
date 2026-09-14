"""FastMCP server exposing the local Modal CLI as tools."""

from fastmcp import FastMCP

from . import tools
from ._cli import read_only

INSTRUCTIONS = """\
Tools for inspecting and operating a Modal.com workspace through the `modal`
CLI installed on this machine: apps, functions, logs, containers, sandboxes,
volumes, secrets and cost.

Start with `whoami` to confirm which workspace and environment you are acting
on. Most tools take an optional `environment`; omitted, they use the CLI's
default.

Apps can be addressed by their deployed name (e.g. "my-app") or app ID
(e.g. "ap-..."). Use `list_apps` to discover both.

Anything not covered by a named tool can be run with `modal_cli`, which takes a
raw argument string (e.g. "app list --json"). Modal adds CLI features
regularly, so consult `modal_cli(args="--help")` rather than assuming.

Sandbox and Function-call tools take noticeably longer than the rest: the CLI
has no subcommand for them, so they run a short generated script.
"""

# Tools that only read state.
READ_TOOLS = [
    tools.whoami,
    tools.list_environments,
    tools.get_workspace_costs,
    tools.list_apps,
    tools.get_app,
    tools.get_app_logs,
    tools.get_deployment_history,
    tools.get_function_stats,
    tools.list_containers,
    tools.list_sandboxes,
    tools.list_volumes,
    tools.list_volume_files,
    tools.read_volume_file,
    tools.list_secrets,
    tools.list_dicts,
    tools.list_queues,
]

# Tools that change state or spend compute.
WRITE_TOOLS = [
    tools.call_function,
    tools.spawn_function,
    tools.get_function_call_result,
    tools.cancel_function_call,
    tools.create_sandbox,
    tools.sandbox_exec,
    tools.terminate_sandbox,
    tools.create_secret,
    tools.stop_app,
    tools.stop_container,
    # Raw CLI access can do anything the CLI can, so it counts as a write tool.
    tools.modal_cli,
]


def build_mcp() -> FastMCP:
    mcp = FastMCP(name="modal", instructions=INSTRUCTIONS)
    for fn in READ_TOOLS:
        mcp.tool(fn)
    if not read_only():
        for fn in WRITE_TOOLS:
            mcp.tool(fn)
    return mcp


def main() -> None:
    """Entry point: serve over stdio against the local Modal CLI."""
    build_mcp().run()


if __name__ == "__main__":
    main()
