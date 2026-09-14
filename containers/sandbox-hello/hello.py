#!/usr/bin/env python3
"""The example app: say hello, and report the environment it found itself in.

Run it anywhere (`python3 hello.py`), inside the flake's devShell
(`nix develop`), or on Modal (`scripts/deploy-hello`). The output is the same
shape everywhere, which is the point -- it is how you tell the three apart.
"""

import os
import platform
import shutil
import sys

# The tools flake.nix puts on PATH, plus two the base image provides. Which
# came from where is the interesting part, so both are reported.
DEVSHELL_TOOLS = ["hello", "jq", "rg", "git", "python3"]


def report():
    yield "hello from " + os.environ.get("HELLO_WHO", "nowhere in particular")
    yield f"python   {platform.python_version()} at {sys.executable}"
    yield f"host     {platform.node()} ({platform.machine()})"
    yield f"devshell {os.environ.get('IN_NIX_SHELL', 'no')}"
    for tool in DEVSHELL_TOOLS:
        yield f"  {tool:8} {shutil.which(tool) or '-- not on PATH'}"


def main():
    for line in report():
        print(line)


if __name__ == "__main__":
    main()
