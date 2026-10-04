# MCP server

Modal ships a Python SDK but no MCP server. `modal-mcp` fills that gap so any
MCP client (Claude Code, a claude.ai custom connector, or another agent) can
inspect and operate a Modal workspace.

It calls the public Modal Python SDK directly. MCP requests never invoke the
Modal CLI, launch local subprocesses, generate Python scripts, or expose an
arbitrary command escape hatch.

The code lives in [`mcp/`](https://github.com/codegod100/modal/tree/main/mcp);
the Modal entrypoint is `mcp/app.py`.

## Two ways to run it

| Mode | Transport | Credentials |
| --- | --- | --- |
| Local | stdio | Your existing Modal profile (`modal token new`) |
| Hosted on Modal | streamable HTTP at `/mcp` | Each caller signs in with Modal; their own token runs their requests |

The hosted server binds every request to that caller's Modal credentials, never
to the host container's identity. Missing or malformed hosted credentials fail
closed, and a fresh SDK client is closed when each operation finishes.

## Requirements

- Modal **1.6 or newer**
- FastMCP 4
- Python 3.10+

## Where to next

- [Connect](connect.md) a client to the hosted server or run it locally.
- [Deploy](deploy.md) your own copy to Modal.
- Browse the [tool reference](tools.md).
