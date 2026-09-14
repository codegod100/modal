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

        # "function" (the default) or "sandbox". Sandboxes can run on a real
        # VM, which Functions cannot -- see ../README.md.
        self.runtime = spec.get("container", {}).get("runtime", "function")

        nix = spec.get("nix", {})
        self.use_flake = bool(nix.get("flake", False))
        self.use_shim = bool(nix.get("shim", False))

        # name -> mount path. Modal Volumes, mounted while the container runs
        # and NOT while its image is built: a volume mount is not part of the
        # resulting image, so anything written to one during a build step is
        # gone by the time the container starts. Persist across runs is the
        # whole point -- a nix store to substitute from, a cargo target
        # directory, a dataset too big to bake in.
        self.volume_spec = dict(spec.get("volumes", {}))

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
        if self.runtime not in ("function", "sandbox"):
            raise SpecError(
                f'[container] runtime must be "function" or "sandbox",'
                f" not {self.runtime!r}"
            )
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
        for name, mount in self.volume_spec.items():
            if not isinstance(mount, str) or not mount.startswith("/"):
                raise SpecError(
                    f"[volumes] {name} must be an absolute path, not {mount!r}"
                )
            # Mounting over one of these hides what the image already has
            # there -- /nix in particular, where the empty volume would shadow
            # the store the base image spent its build populating.
            if mount.rstrip("/") in ("", "/nix", "/nix/store", "/usr", "/etc"):
                raise SpecError(
                    f"[volumes] {name} may not mount over {mount} --"
                    " it would hide what the image has there"
                )
            if mount.rstrip("/") == self.workdir.rstrip("/"):
                raise SpecError(
                    f"[volumes] {name} may not mount over the workdir"
                    f" ({mount}) -- [build] include copies land there"
                )

    # -- image -----------------------------------------------------------

    @property
    def nix(self) -> str:
        """The `nix` command as the container runs it.

        A sandbox on a real VM has a working pty, so the shim buys nothing
        there and is left off even when the image was built with it.
        """
        if self.use_shim and not self.vm_at_runtime:
            return f"LD_PRELOAD={SHIM_SO} nix"
        return "nix"

    @property
    def vm_at_runtime(self) -> bool:
        """True when this container actually runs on a VM rather than gVisor."""
        return (self.runtime == "sandbox"
                and bool(self.experimental_options.get("vm_runtime")))

    @property
    def build_nix(self) -> str:
        """The `nix` command for BUILD steps, which always run under gVisor.

        Image builds are Functions underneath, so a sandbox container still
        needs the shim while its image is being built -- only its run time
        gets the VM.
        """
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
                f"cd {self.workdir} && {self.build_nix} develop"
                " --accept-flake-config --command true",
                f'echo "store paths after warming:'
                f' $({self.build_nix} path-info --all | wc -l)"',
            )

        # copy=True throughout: later run_commands need these files present.
        # `context` is what include paths are relative to, and it may sit above
        # the container directory -- a container that builds the repo it lives
        # in sets context = "../..", so include = ["."] means the whole repo.
        context = os.path.normpath(os.path.join(self.dir, build.get("context", ".")))
        for rel in build.get("include", ["."]):
            src = os.path.normpath(os.path.join(context, rel))
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

    # -- volumes ---------------------------------------------------------

    @property
    def volumes(self) -> dict:
        """{mount path: Volume}, as both Sandbox.create and @app.function want.

        `from_name` is lazy, so this is safe to evaluate on the re-import
        inside the container as well as out here. create_if_missing means a
        spec naming a volume that does not exist yet makes it rather than
        failing -- the first run of a cache is the one that fills it.
        """
        return {
            mount: modal.Volume.from_name(name, create_if_missing=True)
            for name, mount in self.volume_spec.items()
        }

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
        # Deliberately NOT self.experimental_options: that fills in the
        # sandbox default, and vm_runtime on a Function is refused by the
        # server. A sandbox container's @app.function is vestigial anyway.
        if self.runtime == "function":
            if experimental := dict(self.spec.get("experimental", {})):
                kwargs["experimental_options"] = experimental
        if volumes := self.volumes:
            kwargs["volumes"] = volumes
        return kwargs

    @property
    def experimental_options(self) -> dict:
        """Experimental options, with the sandbox default filled in.

        `vm_runtime` is Sandbox-only: the server rejects it on a Function
        outright. So it is defaulted on for sandboxes and never for functions,
        and an explicit [experimental] table always wins.
        """
        explicit = dict(self.spec.get("experimental", {}))
        if explicit:
            return explicit
        if self.runtime == "sandbox":
            return {"vm_runtime": True}
        return {}

    @property
    def sandbox_kwargs(self) -> dict:
        """Everything `Sandbox.create` should be given, from [resources]."""
        r = self.spec.get("resources", {})
        kwargs: dict = {"timeout": int(r.get("timeout", 900))}
        if r.get("cpu"):
            kwargs["cpu"] = float(r["cpu"])
        if r.get("memory"):
            # A VM sandbox gets exactly this much and cannot grow into more.
            kwargs["memory"] = int(r["memory"])
        if opts := self.experimental_options:
            kwargs["experimental_options"] = dict(opts)
        if volumes := self.volumes:
            kwargs["volumes"] = volumes
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

    def run_sandbox(self, override: str = "") -> str:
        """Run one command in a Sandbox that dies when the command does.

        The command IS the sandbox's process, rather than something exec'd
        into a `sleep infinity` box that then has to be torn down. There is no
        idle window to pay for and nothing to leak if this script is killed;
        the [resources] timeout is a backstop, not the mechanism.

        Modal streams a Sandbox's output into the app log as it runs -- which
        is what you want for a build -- so this returns "" rather than handing
        back a copy for the caller to print underneath it.
        """
        line = self.shell_command(override)
        sb = modal.Sandbox.create(
            "sh", "-c", line,
            app=self.app,
            image=self.image,
            workdir=self.workdir,
            env={k: str(v) for k, v in self.env.items()},
            **self.sandbox_kwargs,
        )
        sb.wait()
        if sb.returncode != 0:
            raise RuntimeError(
                f"{self.name}: command failed ({sb.returncode})\n"
                f"$ {line}\n{sb.stderr.read()}"
            )
        return ""

    def open_sandbox(self) -> "modal.Sandbox":
        """Start a Sandbox and leave it running, for `scripts/shell`."""
        return modal.Sandbox.create(
            "sleep",
            "infinity",
            app=self.app,
            image=self.image,
            **self.sandbox_kwargs,
        )

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
