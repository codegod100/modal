"""Runs the local `modal` CLI as a subprocess.

The MCP server is not itself a Modal API client. It drives the CLI that is
already installed and authenticated on this machine, so there are no tokens to
manage and no second implementation of Modal's API to keep current.
"""

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT = 120


class ModalCLIError(RuntimeError):
    """A `modal` command exited non-zero."""

    def __init__(self, args: list[str], returncode: int, stdout: str, stderr: str):
        self.args_ = args
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        detail = (stderr.strip() or stdout.strip() or "no output").splitlines()
        # The CLI renders errors in a rich box; the last lines carry the message.
        message = " ".join(line.strip() for line in detail[-4:] if line.strip())
        super().__init__(f"`modal {' '.join(args)}` failed ({returncode}): {message}")


def modal_bin() -> str:
    """Locate the modal executable.

    Prefers the one beside the interpreter running this server, so a venv
    install is used ahead of anything else on PATH.
    """
    override = os.environ.get("MODAL_MCP_MODAL_BIN")
    if override:
        return override
    candidate = Path(sys.executable).parent / "modal"
    if candidate.is_file():
        return str(candidate)
    found = shutil.which("modal")
    if found:
        return found
    raise ModalCLIError(
        ["--version"],
        127,
        "",
        "Could not find the `modal` executable. Install it with `pip install modal` "
        "or point MODAL_MCP_MODAL_BIN at it.",
    )


async def run(
    *args: str,
    timeout: int = DEFAULT_TIMEOUT,
    check: bool = True,
) -> tuple[int, str, str]:
    """Run `modal <args>` and return (returncode, stdout, stderr)."""
    argv = [a for a in args if a is not None]
    proc = await asyncio.create_subprocess_exec(
        modal_bin(),
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        # Keep the CLI's output machine-readable and non-interactive.
        env={**os.environ, "TERM": "dumb", "NO_COLOR": "1", "COLUMNS": "200"},
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise ModalCLIError(argv, -1, "", f"timed out after {timeout}s") from None

    stdout, stderr = out.decode(errors="replace"), err.decode(errors="replace")
    if check and proc.returncode != 0:
        raise ModalCLIError(argv, proc.returncode or -1, stdout, stderr)
    return proc.returncode or 0, stdout, stderr


async def run_json(*args: str, timeout: int = DEFAULT_TIMEOUT) -> Any:
    """Run a `modal ... --json` command and parse its stdout."""
    _, stdout, stderr = await run(*args, "--json", timeout=timeout)
    text = stdout.strip()
    if not text:
        return []
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Some commands emit a banner before the JSON body.
        for opener, closer in (("[", "]"), ("{", "}")):
            start, end = text.find(opener), text.rfind(closer)
            if start != -1 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    continue
        raise ModalCLIError(
            list(args), 0, stdout, stderr or "expected JSON on stdout"
        ) from None


def env_args(environment: str | None) -> list[str]:
    """`--env` flag for a tool's optional environment argument."""
    env = environment or os.environ.get("MODAL_ENVIRONMENT")
    return ["--env", env] if env else []


def read_only() -> bool:
    return os.environ.get("MODAL_MCP_READ_ONLY", "").lower() in ("1", "true", "yes")
