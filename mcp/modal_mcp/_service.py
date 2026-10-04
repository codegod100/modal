"""Build a typed service definition without importing caller-supplied code."""

import modal


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
):
    app = modal.App(name)
    command = list(argv)

    @app.server(
        image=modal.Image.from_registry(image, add_python="3.12"),
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
):
    """A web_server function; unlike App.server it can serve without proxy auth."""
    app = modal.App(name)
    command = list(argv)

    @app.function(
        image=modal.Image.from_registry(image, add_python=add_python),
        name="web",
        cpu=cpu,
        memory=memory_mb,
        gpu=gpu,
        min_containers=min_containers,
        max_containers=max_containers,
        serialized=True,
        include_source=False,
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
):
    """A plain function that runs argv per call; callers reach it through the SDK."""
    app = modal.App(name)
    setup = list(setup_argv) if setup_argv else None

    @app.function(
        image=modal.Image.from_registry(image, add_python=add_python),
        name="run",
        cpu=cpu,
        memory=memory_mb,
        gpu=gpu,
        min_containers=min_containers,
        max_containers=max_containers,
        timeout=timeout_seconds,
        serialized=True,
        include_source=False,
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
