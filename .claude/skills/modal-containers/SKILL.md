---
name: modal-containers
description: Create, configure, run and debug Modal containers in the codegod100/modal repo, where each container is a directory under containers/ defined by a container.toml. Use when the user wants to make a new Modal container, run or deploy an existing one, choose between a Sandbox and a Function, put a nix devShell in a container, speed up a slow Modal image build, or understand why nix cannot build derivations under gVisor. Triggers include "new container", "new-container", "modal-deploy", "container.toml", "run this on Modal", "build this in the cloud", "sandbox vs function", "vm_runtime", and the errors "unexpected EOF reading a line", "Unknown experimental option", and "MODAL_FUNCTION_RUNTIME must be set to 'gvisor'".
---

# Modal containers

Every container is a directory under `containers/` defined by a
`container.toml`. `containers/_loader.py` turns that into a `modal.Image` and a
`modal.App`; `container.py` is generated and should not be edited. The full key
reference is `containers/spec.md` -- read it before inventing a key.

## Commands

Both are symlinked onto PATH and work from any directory:

    new-container NAME [--function] [--flake] [--base IMAGE]
    modal-deploy NAME [--deploy] [--rebuild-base] [-c CMD]
    modal-deploy --list

In the repo they are `scripts/new-container` and `scripts/deploy`.

`-c` overrides `[run] command` for one run without redeploying -- the fastest
way to poke at a container. Note that `--` cannot be used as a separator:
jolt strips it before the script sees its arguments.

## Sandbox or Function

`new-container` makes a **Sandbox** by default. That is usually right here.

|            | Sandbox (default)            | Function (`--function`)     |
|---|---|---|
| shape      | a container you run a command in | code Modal imports and calls |
| runtime    | real VM (kernel 6.x)         | gVisor only                 |
| lifetime   | dies when its command exits  | Modal manages it            |
| GPU        | no                           | yes                         |
| memory     | static, exactly as requested | elastic                     |
| drop in    | `modal shell sb-...`         | no                          |

Pick a Sandbox for anything that runs once and exits -- a build, a test run, a
one-shot job. Pick a Function for something served or called repeatedly.

A sandbox container's stub registers **no** `@app.function`. Do not add one:
nothing of the module is imported into a Sandbox, and `vm_runtime` in a
Function's kwargs is rejected by the server.

## The gVisor pty bug

Modal Functions and all image builds run under gVisor, where nix cannot build
a derivation: it reads the builder's pty before the child opens the slave, and
gVisor returns `EIO` where Linux blocks. Nix reads that as EOF and gives up:

    error: unexpected EOF reading a line

`ptyshim.c` works around it; `[nix] shim = true` compiles and `LD_PRELOAD`s it.
See the repo README for the full trace.

Consequences to remember:

* `[nix] flake = true` **requires** `shim = true`. The loader refuses the
  combination up front rather than failing minutes into a build.
* A sandbox container still needs `shim = true`, because the image **build**
  runs under gVisor even though the container later runs on a VM. The loader
  uses the shim for build steps and drops it from the run-time command.
* `vm_runtime` is **Sandbox-only**. On a Function the server answers
  `Unknown experimental option: vm_runtime`, and `MODAL_FUNCTION_RUNTIME=runc`
  is refused with `must be set to 'gvisor'`. There is no VM for Functions.

## Build speed

The devShell is warmed at build time so its closure bakes into the image.
Everything in it is binary-cached, so that is a one-time download -- but only
if the layer survives. `_loader.py` copies `flake.nix` and `flake.lock`, warms,
and **only then** copies the source. Never reorder that: copy the source first
and every edit invalidates the warm and re-fetches the whole closure.

Measured on `hello` (~3000 store paths):

| change                          | cost                        |
|---|---|
| cold, or `flake.nix` edited     | ~55s warm                   |
| source edited, flake untouched  | ~6s, warm cached            |
| nothing changed                 | no build                    |

At run time, `nix develop` re-evaluates the flake on **every** start, ~3-4s,
against a VM floor of ~3s. Sandbox `create` is ~0.2s warm and `terminate` ~0.1s;
neither is worth optimising. If the 4s matters, bake the environment with
`nix print-dev-env` at build time and source it instead of entering the shell.

## Gotchas

* A Function container re-imports `container.py` inside the container at
  `/root`, which gets none of the workdir copies. The loader ships `_loader.py`
  and `container.toml` there explicitly and skips validation and image-building
  when `MODAL_TASK_ID` is set. Anything else read at module scope must be
  shipped the same way.
* `modal deploy` on a sandbox container publishes nothing useful -- there is no
  Function to publish.
* Every `modal run` leaves a stopped app row in `modal app list`. Prefer
  `modal-deploy hello -c '...'` over generating throwaway containers, which
  seed that list permanently.
* `modal bootstrap` is not a container scaffolder: it only offers three fixed
  ML demos.
