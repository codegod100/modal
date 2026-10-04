# Containers

One directory per container, each defined by a single `container.toml`.

## Commands

    scripts/new-container NAME      # scaffold a new one
    scripts/deploy NAME             # build + run it once
    scripts/deploy NAME -c 'CMD'    # override [run] command for one run
    scripts/deploy --list           # what is here

Both resolve the repo through their own symlink, so they can live on PATH:

    ln -s "$PWD/scripts/new-container" ~/.local/bin/new-container
    ln -s "$PWD/scripts/deploy"        ~/.local/bin/modal-deploy

`.claude/skills/modal-containers/` documents the whole workflow for Claude.

Each container is a directory holding a `container.toml`, and that file is the
whole definition -- base image, resources, what to run, whether there is a nix
devShell. `containers/_loader.py` turns it into a `modal.Image` and a
`modal.App`; `container.py` is a fixed stub that wires the two together and is
not meant to be edited. the [container.toml reference](spec.md) documents every key.

Modal itself has no such convention -- `modal bootstrap` only stamps out three
fixed ML demos -- so this is local to this repo.

`containers/hello` is the worked example: the `arch-nix` image, a devShell with
`hello`, `jq`, `ripgrep`, `git` and `python3`, and an app that prints where each
of those resolved. It runs as a Sandbox on a real VM.

    hello from the devShell
    python   3.14.7 at /nix/store/d64q19q1...-python3-3.14.7/bin/python3
    host     modal (x86_64)
    devshell impure
      hello    /nix/store/xl1h9i29...-hello-2.12.3/bin/hello

### Layer order is the whole performance story

The devShell is warmed at build time -- `nix develop --command true` -- so its
store paths bake into the image and a container starts straight into the app.
Everything in that closure is binary-cached, so it is a one-time download, but
only if the layer survives.

It survives because `_loader.py` copies **flake.nix and flake.lock, warms, and
only then copies the source**. Copy the source first and every edit to any file
invalidates the warm, and the whole closure is fetched again on every build.
Measured on `hello`, whose shell is ~3000 store paths:

| build | result |
|---|---|
| cold, or `flake.nix` changed | 55s warm step |
| source edited, flake untouched | warm cached; `COPY . /` only, 6s |
| nothing changed | no build at all, 18s end to end |

`git` alone is 87 paths and 404MB, `python3` another 23 and 220MB. That is a
reason to get the layer order right, not a reason to keep them out of the
shell.

### The container re-imports everything

Modal re-imports the entrypoint inside the container, at `/root`, which gets
none of the workdir copies. So `_loader.py` ships three things there
explicitly: itself via `add_local_python_source`, the `container.toml` it
reads, and -- since none of the local tree exists out there -- a `MODAL_TASK_ID`
check that skips validation and image-building on the remote pass.

### Sandboxes get the VM

`containers/hello` is a sandbox container -- `runtime = "sandbox"` -- so it
runs on a real VM. Verified: kernel `6.12.8+`, and `nix build` works there
with no `LD_PRELOAD` at all.

    scripts/deploy hello
    scripts/deploy hello -c 'cat /proc/version'

The image is still built under gVisor, so `[nix] shim` stays on for the build
and is dropped from the run-time command. For a build that should run and then
exit, a sandbox is the better fit than a function: the command is the
sandbox's own process, so it dies when the build does.

### `[nix] shim`

Warming a devShell means *building* `nix-shell-env`, which is exactly the gVisor
pty bug above. `[nix] flake = true` therefore requires `shim = true`, and the
loader refuses the combination up front rather than failing ten minutes into a
build.
