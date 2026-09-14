"""Runs generated Python through `modal run` for operations the CLI lacks.

Sandboxes and calls into deployed Functions have no `modal` subcommand, so these
are expressed as a small script with a local entrypoint and executed by the CLI.
The script prints its result as one sentinel-prefixed JSON line, which is the
only part of `modal run`'s output we parse -- everything else is progress chrome.

Verified: a Sandbox created under a looked-up (persistent) App outlives the
ephemeral driver App, so create / exec / terminate work across separate calls.
"""

import json
import tempfile
from pathlib import Path

from ._cli import ModalCLIError, run

SENTINEL = "__MODAL_MCP_RESULT__:"

PREAMBLE = f'''
import json as _json
import modal

_app = modal.App("modal-mcp-driver")

SENTINEL = {SENTINEL!r}


def _emit(value):
    print(SENTINEL + _json.dumps(value, default=repr))
'''


async def run_script(body: str, timeout: int = 240) -> dict:
    """Execute `body` as a modal local entrypoint and return what it emitted.

    `body` is the indented contents of the entrypoint. Call `_emit(obj)` in it
    exactly once with the result.
    """
    source = (
        PREAMBLE
        + "\n\n@_app.local_entrypoint()\ndef main():\n"
        + "\n".join(f"    {line}" if line.strip() else "" for line in body.splitlines())
        + "\n"
    )

    with tempfile.TemporaryDirectory(prefix="modal-mcp-") as tmp:
        path = Path(tmp) / "driver.py"
        path.write_text(source)
        _, stdout, stderr = await run("run", str(path), timeout=timeout)

    for line in stdout.splitlines():
        if line.startswith(SENTINEL):
            return json.loads(line[len(SENTINEL) :])
    raise ModalCLIError(
        ["run", "<generated>"],
        0,
        stdout,
        stderr or "script produced no result line",
    )


def py(value) -> str:
    """Render a Python literal for embedding in a generated script."""
    return repr(value)
