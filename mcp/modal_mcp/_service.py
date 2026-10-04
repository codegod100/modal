"""Build a typed service definition without importing caller-supplied code."""

from dataclasses import dataclass, field

import modal


@dataclass(frozen=True)
class Attachments:
    """Named Modal objects and build steps a deployed app may use.

    volumes maps an absolute mount path to a Volume name (created if missing);
    secrets are Secret names whose keys become environment variables; and
    image_commands run in the image build on Modal, never on the MCP host.
    Names resolve in the environment the app deploys to.
    """

    volumes: dict[str, str] = field(default_factory=dict)
    secrets: tuple[str, ...] = ()
    image_commands: tuple[str, ...] = ()


def _image(image: str, add_python: str | None, attach: Attachments | None):
    built = modal.Image.from_registry(image, add_python=add_python)
    if attach and attach.image_commands:
        built = built.run_commands(*attach.image_commands)
    return built


def _mounts(attach: Attachments | None) -> dict:
    options: dict = {}
    if attach and attach.volumes:
        options["volumes"] = {
            path: modal.Volume.from_name(name, create_if_missing=True)
            for path, name in attach.volumes.items()
        }
    if attach and attach.secrets:
        options["secrets"] = [modal.Secret.from_name(name) for name in attach.secrets]
    return options


def service_app(
    name: str,
    image: str,
    argv: list[str],
    port: int,
    cpu: float,
    memory_mb: int,
    min_containers: int,
    max_containers: int,
    startup_timeout_seconds: int,
    gpu: str | None = None,
    *,
    attach: Attachments | None = None,
):
    app = modal.App(name)
    command = list(argv)

    @app.server(
        image=_image(image, "3.12", attach),
        name="service",
        port=port,
        cpu=cpu,
        memory=memory_mb,
        gpu=gpu,
        min_containers=min_containers,
        max_containers=max_containers,
        startup_timeout=startup_timeout_seconds,
        serialized=True,
        include_source=False,
        unauthenticated=False,
        **_mounts(attach),
    )
    class Service:
        @modal.enter()
        def start(self):
            # This hook is serialized and runs only in the deployed container.
            # argv is passed directly, without a shell or host-side execution.
            import subprocess

            self.process = subprocess.Popen(command)

        @modal.exit()
        def stop(self):
            self.process.terminate()

    return app


def web_function_app(
    name: str,
    image: str,
    argv: list[str],
    port: int,
    cpu: float,
    memory_mb: int,
    min_containers: int,
    max_containers: int,
    startup_timeout_seconds: int,
    public: bool,
    add_python: str | None,
    gpu: str | None = None,
    *,
    attach: Attachments | None = None,
):
    """A web_server function; unlike App.server it can serve without proxy auth."""
    app = modal.App(name)
    command = list(argv)

    @app.function(
        image=_image(image, add_python, attach),
        name="web",
        cpu=cpu,
        memory=memory_mb,
        gpu=gpu,
        min_containers=min_containers,
        max_containers=max_containers,
        serialized=True,
        include_source=False,
        **_mounts(attach),
    )
    @modal.web_server(port, startup_timeout=startup_timeout_seconds, requires_proxy_auth=not public)
    def web():
        # Serialized and run only in the deployed container. argv is passed
        # directly, without a shell or host-side execution.
        import subprocess

        subprocess.Popen(command)

    return app


def command_function_app(
    name: str,
    image: str,
    setup_argv: list[str] | None,
    cpu: float,
    memory_mb: int,
    gpu: str | None,
    min_containers: int,
    max_containers: int,
    timeout_seconds: int,
    add_python: str | None,
    *,
    attach: Attachments | None = None,
):
    """A plain function that runs argv per call; callers reach it through the SDK."""
    app = modal.App(name)
    setup = list(setup_argv) if setup_argv else None

    @app.function(
        image=_image(image, add_python, attach),
        name="run",
        cpu=cpu,
        memory=memory_mb,
        gpu=gpu,
        min_containers=min_containers,
        max_containers=max_containers,
        timeout=timeout_seconds,
        serialized=True,
        include_source=False,
        **_mounts(attach),
    )
    def run(argv: list[str], output_file: str | None = None) -> dict:
        # Serialized and run only in the deployed container. setup runs once per
        # container; argv is passed directly, without a shell or host-side execution.
        import base64
        import os
        import subprocess

        marker = "/tmp/.modal-mcp-setup-done"
        if setup and not os.path.exists(marker):
            done = subprocess.run(setup, capture_output=True, text=True, check=False)
            if done.returncode:
                return {
                    "stage": "setup",
                    "returncode": done.returncode,
                    "stdout": done.stdout[-20000:],
                    "stderr": done.stderr[-20000:],
                }
            open(marker, "w").close()
        proc = subprocess.run(argv, capture_output=True, text=True, check=False)
        result = {
            "stage": "run",
            "returncode": proc.returncode,
            "stdout": proc.stdout[-20000:],
            "stderr": proc.stderr[-20000:],
        }
        if output_file and proc.returncode == 0 and os.path.isfile(output_file):
            with open(output_file, "rb") as f:
                result["output_base64"] = base64.b64encode(f.read()).decode()
        return result

    return app
