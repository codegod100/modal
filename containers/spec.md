# `container.toml` -- the container spec

Every directory under `containers/` is one container, and it is defined by a
`container.toml`. `containers/_loader.py` reads that file and produces the
`modal.Image` and the `modal.App`; `container.py` is a generated stub that
wires the two together. Nothing else is hand-written per container.

There are two stub shapes, one per runtime. A function container registers an
`@app.function` and calls it; a sandbox container registers none at all --
nothing of your module is imported into a Sandbox, so a Function there would
be dead weight, and its kwargs are exactly where `vm_runtime` would be wrongly
applied.

Create one with `scripts/new-container <name>`, deploy it with
`scripts/deploy <name>`.

## Every key

Only `[container] name` is required. Everything below shows its default.

```toml
[container]
name        = "hello"          # required; the Modal App name
description = ""               # shown in the generated README and --help
runtime     = "function"       # "function" (the loader default) or "sandbox".
                               # scripts/new-container defaults to "sandbox"
                               # and always writes this key explicitly, so the
                               # loader default rarely applies.

# Exactly one of `base` (a published Modal image, from `.publish()`) or
# `registry` (a Docker registry reference). `base` is the usual one here.
base        = "arch-nix"
# registry  = "archlinux:base-devel"

[build]
include  = ["."]               # paths, relative to this directory, copied to workdir
commands = []                  # extra RUN steps, in order, after the copy

[resources]                    # passed straight to @app.function
cpu     = 0                    # 0 / omitted means "let Modal decide"
memory  = 0                    # MiB
timeout = 900                  # seconds
gpu     = ""

[run]
workdir = "/app"
command = "python3 hello.py"   # what the container actually runs
env     = { }                  # environment variables

[nix]
flake = false                  # warm `nix develop` at build time, run inside it
shim  = false                  # build ptyshim.so and LD_PRELOAD every nix call

[volumes]                      # name = mount path; Modal Volumes, at run time
# nix-cache = "/nix-cache"

[experimental]                 # verbatim into @app.function(experimental_options=)
# vm_runtime = true
```

## `[volumes]` -- state that outlives the container

Each key is a Modal Volume name and each value an absolute mount path. The
volume is created if it does not exist, so the first run of a cache is the one
that fills it.

```toml
[volumes]
nix-cache = "/nix-cache"
```

Mounted while the container **runs**, and not while its image is built. That is
not an omission: a volume mount is not part of the resulting image, so anything
written to one during a build step is gone by the time the container starts.
Build-time population is `arch_nix.py`'s job, where paths are copied *out* of a
volume and into the store so they bake in.

Refused at validation: a relative path, and a mount over `/nix`, `/nix/store`,
`/usr`, `/etc` or the workdir. An empty volume over any of those hides what the
image already has there -- `/nix` being the expensive one, since the base image
spent its build populating that store.

Volumes are commit/reload rather than POSIX. Nothing written is durable until
something calls `.commit()`, two containers writing one volume is last-write-
wins with no locking, and a volume used as a nix cache needs the substituter
pointed at it as well as mounted -- mounting alone changes nothing:

```toml
[run]
command = """nix build path:/app#frq --extra-substituters file:///nix-cache \
  && nix copy --no-check-sigs --to file:///nix-cache ./result"""
```

## `runtime` -- Function or Sandbox

A **Function** is code Modal runs for you: it imports your module in the
container and you call it like a function. Autoscaled, warm-pooled, and
gVisor-only.

A **Sandbox** is a container you start and run commands in. Nothing of yours is
imported. You control its lifetime, and it can run on a real VM -- which
Functions cannot, at all.

|                | `function`            | `sandbox`                     |
|---|---|---|
| invocation     | `f.remote(args)`      | the command is the process    |
| runtime        | gVisor only           | gVisor or real VM             |
| lifetime       | Modal decides         | dies when the command exits   |
| GPU            | yes                   | no, on a VM                   |
| memory         | elastic               | static, exactly what you ask  |

`runtime = "sandbox"` turns on `vm_runtime` by default, and an explicit
`[experimental]` table overrides that. For a build that should run and then
die, `sandbox` is the right answer: the command *is* the sandbox's process, so
there is no idle window and nothing to tear down.

Note that `vm_runtime` is **Sandbox-only** -- the server rejects it on a
Function outright -- so the loader never passes it to `@app.function`, even for
a sandbox container whose vestigial function still gets registered.

## `[nix]`

`flake = true` requires a `flake.nix` in the container directory with a
`devShells.default`. The build warms it once so its store paths bake into the
image, and `[run] command` then runs inside `nix develop`.

`shim` exists because warming that shell means *building* the `nix-shell-env`
derivation, which is exactly the gVisor pty bug in `../README.md`. With
`flake = true` and `shim = false` the build fails at that step. The generator
turns both on together.

`shim` requires `base` to be an image with a C compiler on it -- `arch-nix`
has one.

A sandbox container on a VM still needs `shim = true`, because image **builds**
run under gVisor regardless of what the container later runs on. The loader
uses the shim for build steps and drops it from the run-time command, since a
real VM has a working pty.

## What the loader guarantees

* the App is named `[container] name`
* the function is called `run`, takes an optional `command` override, and
  returns what the command printed
* a `main` local entrypoint prints that, so `modal run` just works
* a non-zero exit is raised as a `RuntimeError` carrying stderr
