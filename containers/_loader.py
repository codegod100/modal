"""Turn a `container.toml` into a `modal.Image` and a `modal.App`.

Every container under `containers/` is a directory with a `container.toml` and
a stub `container.py`. See `spec.md` for the keys; this file is what reads
them. Nothing here is Modal-specific configuration in its own right -- each
spec key maps onto a documented Modal argument, and the mapping is meant to
stay boring enough to read straight through.
"""

import os
import shlex
import subprocess
import tomllib

import modal

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PTYSHIM_C = os.path.join(REPO, "ptyshim.c")
SHIM_SO = "/opt/ptyshim.so"


class SpecError(Exception):
    """The container.toml says something that cannot be built."""


def _running_in_modal() -> bool:
    """True inside a Modal container, false on the machine that launched it."""
    return bool(os.environ.get("MODAL_TASK_ID"))


class Container:
    """One container: its spec, its image, its app, and how to run it."""

    def __init__(self, spec: dict, directory: str):
        self.is_remote = _running_in_modal()
        self.dir = directory
        self.spec = spec
        self.name = self._require("container", "name")
        self.description = spec.get("container", {}).get("description", "")

        run = spec.get("run", {})
        self.workdir = run.get("workdir", "/app")
        self.command = run.get("command", "")
        self.env = dict(run.get("env", {}))

        nix = spec.get("nix", {})
        self.use_flake = bool(nix.get("flake", False))
        self.use_shim = bool(nix.get("shim", False))

        # Modal re-imports this module inside the container, so everything
        # below runs twice: once here, once out there. Out there the local
        # tree does not exist -- no flake.nix, no ptyshim.c, no repo -- and
        # the image is already built, so validating and rebuilding it would
        # only fail. The App still has to exist for the decorators to bind.
        if self.is_remote:
            self.image = None
            self.app = modal.App(self.name)
        else:
            self._validate()
            self.image = self._build_image()
            self.app = modal.App(self.name, image=self.image)

    # -- spec reading ----------------------------------------------------

    @classmethod
    def from_toml(cls, container_py: str) -> "Container":
        """Load the container.toml sitting next to the given container.py."""
        directory = os.path.dirname(os.path.abspath(container_py))
        path = os.path.join(directory, "container.toml")
        if not os.path.exists(path):
            raise SpecError(f"no container.toml in {directory}")
        with open(path, "rb") as f:
            return cls(tomllib.load(f), directory)

    def _require(self, table: str, key: str):
        try:
            return self.spec[table][key]
        except KeyError:
            raise SpecError(f"container.toml needs [{table}] {key}") from None

    def _validate(self):
        c = self.spec.get("container", {})
        if bool(c.get("base")) == bool(c.get("registry")):
            raise SpecError("set exactly one of [container] base or registry")
        if self.use_flake:
            if not os.path.exists(os.path.join(self.dir, "flake.nix")):
                raise SpecError("[nix] flake = true but there is no flake.nix")
            if not self.use_shim:
                # Warming the shell builds nix-shell-env, which is the gVisor
                # pty bug. Failing here beats failing ten minutes into a build.
                raise SpecError(
                    "[nix] flake = true needs shim = true -- see ../README.md"
                )
        if self.use_shim and not os.path.exists(PTYSHIM_C):
            raise SpecError(f"[nix] shim = true but {PTYSHIM_C} is missing")

    # -- image -----------------------------------------------------------

    @property
    def nix(self) -> str:
        """The `nix` command, shimmed if the spec asks for it."""
        return f"LD_PRELOAD={SHIM_SO} nix" if self.use_shim else "nix"

    def _build_image(self) -> modal.Image:
        c = self.spec["container"]
        build = self.spec.get("build", {})

        if c.get("base"):
            image = modal.Image.from_name(c["base"])
        else:
            image = modal.Image.from_registry(c["registry"])

        if self.use_shim:
            image = image.add_local_file(
                PTYSHIM_C, "/opt/ptyshim.c", copy=True
            ).run_commands(
                f"gcc -shared -fPIC -O2 -o {SHIM_SO} /opt/ptyshim.c -ldl"
            )

        # Warm the devShell BEFORE the source is copied in. Everything the
        # shell needs is binary-cached, so the download happens once -- but
        # only if this layer survives. Copy the source first and any edit to
        # any file invalidates the warm, and the whole closure is fetched
        # again on every build. Only flake.nix and flake.lock go in here, so
        # the layer is invalidated by a dependency change and nothing else.
        if self.use_flake:
            for f in ("flake.nix", "flake.lock"):
                path = os.path.join(self.dir, f)
                if os.path.exists(path):
                    image = image.add_local_file(
                        path, f"{self.workdir}/{f}", copy=True
                    )
            image = image.run_commands(
                f"cd {self.workdir} && {self.nix} develop"
                " --accept-flake-config --command true",
                f'echo "store paths after warming:'
                f' $({self.nix} path-info --all | wc -l)"',
            )

        # copy=True throughout: later run_commands need these files present.
        for rel in build.get("include", ["."]):
            src = os.path.join(self.dir, rel)
            dest = self.workdir if rel == "." else f"{self.workdir}/{rel}"
            if os.path.isdir(src):
                image = image.add_local_dir(src, dest, copy=True)
            else:
                image = image.add_local_file(src, dest, copy=True)

        if commands := build.get("commands", []):
            image = image.run_commands(*commands)

        # container.py does `from _loader import Container`, and Modal mounts
        # the entrypoint file alone -- so without this the import that works
        # locally fails in the container. copy=False adds it at startup rather
        # than baking a layer, so it invalidates nothing above it, and it must
        # therefore come after every build step.
        image = image.add_local_python_source("_loader")
        # ...and container.py reads its spec at import time, so the spec has to
        # be there too. Modal re-imports the entrypoint at /root, which is the
        # one directory that gets none of the workdir copies above.
        image = image.add_local_file(
            os.path.join(self.dir, "container.toml"), "/root/container.toml"
        )

        return image

    # -- function --------------------------------------------------------

    @property
    def function_kwargs(self) -> dict:
        """Everything `@app.function` should be given, from [resources]."""
        r = self.spec.get("resources", {})
        kwargs: dict = {"timeout": int(r.get("timeout", 900))}
        if r.get("cpu"):
            kwargs["cpu"] = float(r["cpu"])
        if r.get("memory"):
            kwargs["memory"] = int(r["memory"])
        if r.get("gpu"):
            kwargs["gpu"] = r["gpu"]
        if experimental := self.spec.get("experimental", {}):
            kwargs["experimental_options"] = dict(experimental)
        return kwargs

    def shell_command(self, override: str = "") -> str:
        """The full shell line the container runs, devShell wrapper included."""
        command = override or self.command
        if not command:
            raise SpecError("container.toml has no [run] command")
        if self.use_flake:
            return (
                f"cd {self.workdir} && {self.nix} develop --accept-flake-config"
                f" --command sh -c {shlex.quote(command)}"
            )
        return f"cd {self.workdir} && {command}"

    def execute(self, override: str = "") -> str:
        """Run the command in this container. Called remotely, not locally."""
        line = self.shell_command(override)
        result = subprocess.run(
            line,
            shell=True,
            capture_output=True,
            text=True,
            env={**os.environ, **{k: str(v) for k, v in self.env.items()}},
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"{self.name}: command failed ({result.returncode})\n"
                f"$ {line}\n{result.stderr}"
            )
        return result.stdout
