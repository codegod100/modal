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

### Alternatives not tried

* `experimental_options={"vm_runtime": True}` runs a Sandbox on a real VM
  instead of gVisor, where the pty should behave normally. Documented for
  Sandboxes; unclear whether image builds can opt in.
* Older nix wrote the handshake down a plain pipe rather than a pty. Arch ships
  2.35.2. Installing 2.24/2.28 from the Arch archive failed on
  `libboost_context` and `liblowdown` version pins.

## `examples/hello` -- an app in a devShell, on Modal

    scripts/deploy-hello                    # build + run once, print the output
    scripts/deploy-hello --who "my laptop"  # pass a greeting through
    scripts/deploy-hello --deploy           # publish the app instead
    scripts/deploy-hello --rebuild-base     # republish arch-nix first

`scripts/deploy-hello` is a jolt script; it only checks that `modal` is around
and that the base image exists, then shells out to `modal`.

The container is **built on the published `arch-nix` image** -- nix,
substituters and trusted keys already configured there, nothing re-derived.
`examples/hello/modal_app.py` adds three layers on top: this directory, a
`ptyshim.so` compiled from `ptyshim.c`, and one `nix develop --command true`
that warms the devShell so its store paths bake in. The shim is what makes that
last step possible at all; without it the `nix-shell-env` derivation hits the
gVisor pty bug above.

`examples/hello/flake.nix` is a plain `mkShell` with `hello`, `jq`, `ripgrep`,
`git` and `python3` on PATH, and `HELLO_WHO` set. `hello.py` prints where each
of those resolved, so the same command distinguishes the three places it runs:

    hello from the devShell
    python   3.14.7 at /nix/store/d64q19q1…-python3-3.14.7/bin/python3
    host     modal (x86_64)
    devshell impure
      hello    /nix/store/xl1h9i29…-hello-2.12.3/bin/hello

Verified end to end: 3003 store paths after warming, app output as above.

## Other notes

* `add_python=` cannot overlay `/usr/local` on `archlinux:base-devel` -- the
  `COPY /python/. /usr/local` step fails. Install Arch's own `python` and
  `python-pip` in `setup_dockerfile_commands` instead; that is all Modal wants.
* `cache.flakehub.com` needs a token, so it 401s on every lookup from a
  container. Leave it out of `substituters`.
* `nixos/nix` images fail Modal's Python detection even with `add_python=`.
