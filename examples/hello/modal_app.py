"""Run examples/hello on Modal, inside this directory's nix devShell.

    modal run examples/hello/modal_app.py
    scripts/deploy-hello            # the same, with the flags spelled out

The image is built on the already-configured `arch-nix` image -- the one
`modal run arch_nix.py` publishes, with nix and its substituters already set
up -- plus three things: this directory, a compiled ptyshim.so, and a warmed
devShell. The shim is not optional: `nix develop` has to *build* the mkShell
env derivation, and that is exactly what gVisor's pty breaks. See
../../README.md and ../../ptyshim.c.
"""

import os

import modal

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.join(REPO, "examples", "hello")

APP_DIR = "/app"
SHIM = "/opt/ptyshim.so"
# Every nix invocation that builds anything needs the shim in front of it.
NIX = f"LD_PRELOAD={SHIM} nix"

# Published by `modal run arch_nix.py`. Nothing here reconfigures nix; that
# image already has the substituters, the trusted keys and the baked store.
image = (
    modal.Image.from_name("arch-nix")
    .add_local_file(os.path.join(REPO, "ptyshim.c"), "/opt/ptyshim.c", copy=True)
    .run_commands(f"gcc -shared -fPIC -O2 -o {SHIM} /opt/ptyshim.c -ldl")
    # copy=True: later run_commands need these files to already be there.
    .add_local_dir(HERE, APP_DIR, copy=True)
    # Warm the devShell at build time so its store paths bake into the image
    # and a fresh container starts straight into the app.
    .run_commands(
        f"cd {APP_DIR} && {NIX} develop --accept-flake-config --command true",
        f'echo "store paths after warming: $({NIX} path-info --all | wc -l)"',
    )
)

app = modal.App("hello-example", image=image)


@app.function()
def hello(who: str = "a fresh Modal container"):
    """Run hello.py inside the devShell and hand back what it printed."""
    import subprocess

    result = subprocess.run(
        f"cd {APP_DIR} && {NIX} develop --accept-flake-config"
        " --command python3 hello.py",
        shell=True,
        capture_output=True,
        text=True,
        env={**os.environ, "HELLO_WHO": who},
    )
    if result.returncode != 0:
        raise RuntimeError(f"hello.py failed ({result.returncode}):\n{result.stderr}")
    return result.stdout


@app.local_entrypoint()
def main(who: str = "a fresh Modal container"):
    print(hello.remote(who), end="")
