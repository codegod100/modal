# `container.toml` -- the container spec

Every directory under `containers/` is one container, and it is defined by a
`container.toml`. `containers/_loader.py` reads that file and produces the
`modal.Image` and the `modal.App`; `container.py` is a fixed four-line stub
that wires the two together. Nothing else is hand-written per container.

Create one with `scripts/new-container <name>`, deploy it with
`scripts/deploy <name>`.

## Every key

Only `[container] name` is required. Everything below shows its default.

```toml
[container]
name        = "hello"          # required; the Modal App name
description = ""               # shown in the generated README and --help

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

[experimental]                 # verbatim into @app.function(experimental_options=)
# vm_runtime = true
```

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

## What the loader guarantees

* the App is named `[container] name`
* the function is called `run`, takes an optional `command` override, and
  returns what the command printed
* a `main` local entrypoint prints that, so `modal run` just works
* a non-zero exit is raised as a `RuntimeError` carrying stderr
