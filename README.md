# modal

Modal images and the workarounds they needed.

## `arch_nix.py` -- Arch Linux + nix

    modal run arch_nix.py          # verifies nix, publishes arch-nix:latest

Then anywhere:

```python
import modal
image = modal.Image.from_name("arch-nix")
```

Substituters and trusted keys are at the top of the file, along with
`CACHE_PATHS` -- store paths to bake in from a `nix-cache` Modal Volume. The
Volume is mounted **during the image build only**. That is the useful half of
a nix cache here: a volume mount is not part of the resulting image, but
filesystem changes outside it are, so `/nix/store` keeps what was copied while
`/nix-cache` disappears.

`CACHE_PATHS` is empty by default. `nix copy --all` works and was how this was
first proven -- it pulled all 1718 paths in the cache, verified by
`nix path-info --all` at run time with no volume attached -- but it also baked
in a gigabyte of unrelated `rustc`. Name what you need instead.

`run_commands` and `run_function` both take `volumes=`, so a build step can
mount one. Only the final container filesystem is snapshotted.

## Nix cannot build derivations on Modal

Substitution works fine. Building anything -- including the trivial
`mkShell` env derivation that `nix develop` needs -- fails:

    error: … while waiting for the build environment for
           '/nix/store/…-probe.drv' to initialize (succeeded, previous messages: )
           error: unexpected EOF reading a line

This is not a nix.conf problem. `sandbox` on or off, `use-cgroups=false`,
`auto-allocate-uids=false`, `TMPDIR`, `max-jobs=1` all fail identically.

### What is actually happening

Modal runs containers under [gVisor](https://modal.com/docs/guide/sandboxes).
Nix runs every builder behind a pseudo-terminal, and immediately after forking
it reads the pty master for the child's one-byte handshake -- before the child
has opened the slave. On Linux that read blocks. Under gVisor a master with no
open slave returns `EIO`.

Nix treats `EIO` as EOF, gives up, and reports a failure. An strace shows the
builder then running to completion regardless, unheard:

    45  write(2, "\2\n", 2)   = 2          # the handshake nix never sees
    45  execve("/bin/sh", ["sh", "-c", "echo hi > $out"], …) = 0
    45  write(1, "hi\n", 3)   = 3          # the build succeeds
    6   read(24, …)           = -1 EIO     # the parent has already given up

Other gVisor divergences visible in the same trace, none of them fatal here:
`unshare(CLONE_NEWNS)` → `EPERM`, `landlock_create_ruleset` → `ENOSYS`,
`fchmodat2` → `ENOSYS`, `rseq` → `ENOSYS`, `MADV_GUARD_INSTALL` → `EINVAL`.

### `ptyshim.c`

    gcc -shared -fPIC -O2 -o ptyshim.so ptyshim.c -ldl
    LD_PRELOAD=./ptyshim.so nix build …

Retries `EIO` on a pty master *only until that master has ever delivered a
byte*, then steps aside so the real hangup at build end still reaches nix.
Nothing is held open, so builds also finish. Verified on Modal:

| case | result |
|---|---|
| trivial derivation | builds, 0.9s |
| slow multi-line builder | builds, output intact |
| builder exiting 3 | fails cleanly, log captured |
| `nixpkgs#hello` | substitutes and builds |

It is deliberately **not** wired into `arch_nix.py`: that image is generic, and
most uses only need substitution.

### A real VM does fix it -- but only for Sandboxes

`experimental_options={"vm_runtime": True}` works, and the pty behaves. Probed
against the `arch-nix` image, one Sandbox each way:

| | kernel | `nix build` without the shim |
|---|---|---|
| gVisor (default) | `4.19.0-gvisor` | `error: unexpected EOF reading a line` |
| `vm_runtime` | `6.12.8+` | builds |

The catch is scope: [the docs](https://modal.com/docs/guide/vm-sandboxes) say
this is **Sandboxes only, not Functions**. Passing it to `@app.function` is
refused outright -- `Unknown experimental option: vm_runtime` -- and the
underlying `runtime` field ("runc" or "gvisor" in the proto) is refused too:
`MODAL_FUNCTION_RUNTIME must be set to 'gvisor'`.

So for anything built on Functions -- which is everything in `containers/` --
gVisor is mandatory and the shim is permanent. A Sandbox-backed container
would be the way to escape it, at the cost of no GPUs, static memory, and a
512 GiB image cap.
### Alternatives still not tried

* Older nix wrote the handshake down a plain pipe rather than a pty. Arch ships
  2.35.2. Installing 2.24/2.28 from the Arch archive failed on
  `libboost_context` and `liblowdown` version pins.

## `containers/` -- one directory per container

    scripts/new-container NAME      # scaffold a new one
    scripts/deploy NAME             # build + run it once
    scripts/deploy --list           # what is here

Each container is a directory holding a `container.toml`, and that file is the
whole definition -- base image, resources, what to run, whether there is a nix
devShell. `containers/_loader.py` turns it into a `modal.Image` and a
`modal.App`; `container.py` is a fixed stub that wires the two together and is
not meant to be edited. `containers/spec.md` documents every key.

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

## Other notes

* `add_python=` cannot overlay `/usr/local` on `archlinux:base-devel` -- the
  `COPY /python/. /usr/local` step fails. Install Arch's own `python` and
  `python-pip` in `setup_dockerfile_commands` instead; that is all Modal wants.
* `cache.flakehub.com` needs a token, so it 401s on every lookup from a
  container. Leave it out of `substituters`.
* `nixos/nix` images fail Modal's Python detection even with `add_python=`.
