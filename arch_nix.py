"""A plain Arch Linux image with nix on it, for Modal.

    from arch_nix import image             # or: modal.Image.from_name("arch-nix")
    modal run arch_nix.py                  # checks nix, publishes as arch-nix:latest

Nix can *substitute* freely in this image. It cannot *build* a derivation --
see README.md and ptyshim.c.

The `nix-cache` Modal Volume is mounted during the build step only. A volume
mount is not part of the resulting image, but filesystem changes outside it
are -- so paths pulled out of the cache land in /nix/store and bake in, while
/nix-cache itself does not.

Edit SUBSTITUTERS / TRUSTED_KEYS below for your own caches.
"""

import modal

SUBSTITUTERS = [
    "https://cache.nixos.org",
    "https://nix-cache.wasix.org",
    "https://install.determinate.systems",
]

TRUSTED_KEYS = [
    "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY=",
    "wasinix-1:jvsqbOJGsZxMvg97fuyNCWCc+t2nn6uHB47kQCGNmXI=",
]

# Mounted during the image build so its store paths can be copied in.
# Populate it from a machine where nix works:
#     nix copy --to file://./cache <store paths>
#     modal volume put nix-cache ./cache /
nix_cache = modal.Volume.from_name("nix-cache", create_if_missing=True)
CACHE_MOUNT = "/nix-cache"

# Store paths to bake into the image from that cache, as `nix copy` arguments.
# Empty by default, and deliberately: `nix copy --all` will happily pull every
# path the cache has ever held -- 1718 paths and a gigabyte of someone else's
# rustc, in the case this was first written against. Name what you need.
CACHE_PATHS: list[str] = []


def _nix_conf(*extra_substituters):
    return "\n".join([
        "experimental-features = nix-command flakes",
        # root in a container: no daemon, no nixbld group, no sandbox
        "build-users-group =",
        "sandbox = false",
        # Nix's own default is max-jobs = 1, which builds the whole graph end
        # to end on one core while the rest of the box idles. A tail of small
        # derivations -- a thousand crate unpacks, say -- pays that serially.
        # `auto` is one job per core; `cores = 0` then lets each job use every
        # core it can, which oversubscribes on purpose: the cheap derivations
        # that make up the tail never come close to saturating a core each.
        "max-jobs = auto",
        "cores = 0",
        "substituters = " + " ".join([*extra_substituters, *SUBSTITUTERS]),
        "trusted-public-keys = " + " ".join(TRUSTED_KEYS),
        # The volume holds locally-built paths that carry no signature.
        "require-sigs = false",
        "",
    ])

image = (
    modal.Image.from_registry(
        "archlinux:base-devel",
        # Modal wants `python` and `pip` on PATH; Arch's own satisfy it.
        setup_dockerfile_commands=[
            "RUN pacman -Syu --noconfirm python python-pip nix git",
        ],
    )
    .run_commands(
        "mkdir -p /etc/nix",
        # During the build the volume is available, so prefer it.
        f"cat > /etc/nix/nix.conf <<'EOF'\n{_nix_conf('file://' + CACHE_MOUNT)}EOF",
    )
    # Bake the named paths out of the cache and into the image's own store.
    .run_commands(
        f"test -f {CACHE_MOUNT}/nix-cache-info && test -n '{' '.join(CACHE_PATHS)}'"
        f" && nix copy --no-check-sigs --from file://{CACHE_MOUNT} {' '.join(CACHE_PATHS)}"
        f" || echo 'nothing to bake from nix-cache'",
        'echo "store paths in image: $(nix path-info --all | wc -l)"',
        volumes={CACHE_MOUNT: nix_cache},
    )
    # The volume is gone at run time; drop it from the substituter list so
    # containers do not waste a lookup on a path that no longer exists.
    .run_commands(f"cat > /etc/nix/nix.conf <<'EOF'\n{_nix_conf()}EOF")
)

app = modal.App("arch-nix", image=image)


@app.function()
def check():
    import subprocess
    for cmd in [
        "nix --version",
        "nix config show substituters",
        "echo \"store paths baked in: $(nix path-info --all | wc -l)\"",
    ]:
        print(f"$ {cmd}")
        print(subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout)


@app.local_entrypoint()
def main():
    check.remote()
    # Named, so it is visible in the Modal UI and reusable elsewhere as
    # modal.Image.from_name("arch-nix").
    image.build(app)
    image.publish("arch-nix")
