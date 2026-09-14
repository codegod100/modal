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


def caller_env() -> dict[str, str | None]:
    """Environment overrides that run the CLI as the authenticated caller.

    Served over HTTP, each caller signs in with Modal and their own API token is
    what commands run with. Over stdio there is no caller, and the local
    ~/.modal.toml profile is used instead.

    MODAL_IS_REMOTE is cleared deliberately. Inside a container the Modal client
    treats it as authoritative and silently ignores token environment variables,
    which would run every caller's commands with the container's own
    workspace-wide identity -- a failure that is invisible rather than loud.
    """
    try:
        from fastmcp.server.dependencies import get_access_token

        token = get_access_token()
    except Exception:
        return {}
    if token is None:
        return {}
    claims = getattr(token, "claims", None) or {}
    token_id = claims.get("modal_token_id")
    token_secret = claims.get("modal_token_secret")
    if not token_id or not token_secret:
        raise ModalCLIError(
            ["<auth>"], 1, "",
            "Authenticated request carried no Modal credentials; refusing to run "
            "with the server's own identity.",
        )
    return {
        "MODAL_IS_REMOTE": None,
        "MODAL_TOKEN_ID": token_id,
        "MODAL_TOKEN_SECRET": token_secret,
        # The CLI must not fall back to a profile file for a remote caller.
        "MODAL_PROFILE": None,
    }


def _build_env() -> dict[str, str]:
    env = {**os.environ, "TERM": "dumb", "NO_COLOR": "1", "COLUMNS": "200"}
    overrides = caller_env()
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    if overrides and env.get("MODAL_IS_REMOTE") == "1":
        # Belt and braces: never let a caller's command run as the container.
        raise ModalCLIError(
            ["<auth>"], 1, "", "Refusing to run: MODAL_IS_REMOTE survived credential setup."
        )
    return env


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
        env=_build_env(),
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
