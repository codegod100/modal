"""celld on Modal: a Workers + Durable Objects app served from one container.

    modal serve celld/app.py     # dev URL, hot reload of this file
    modal deploy celld/app.py    # stable URL

celld (https://celld.dev) runs a Wrangler project -- here `worker/` -- with
each Durable Object as a "cell" that owns its own SQLite database. This runs
one node in `celld dev` mode, whose object store and cell work files live in
the project's `.celld/dev`. That directory is a Modal Volume, so the rooms
outlive the container.
"""

from pathlib import Path
import subprocess

import modal

CELLD_VERSION = "v0.6.1"
ESBUILD_VERSION = "0.28.2"  # `celld dev` bundles the Worker with esbuild
PORT = 9876
PROJECT = "/app"
HERE = Path(__file__).parent

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("curl", "ca-certificates")
    .run_commands(
        # The release binary, exactly what https://celld.dev/install.sh fetches.
        f"curl -fsSL https://github.com/denoland/celld/releases/download/{CELLD_VERSION}"
        "/celld-x86_64-unknown-linux-gnu.gz | gzip -dc > /usr/local/bin/celld",
        "chmod 755 /usr/local/bin/celld",
        f"curl -fsSL https://registry.npmjs.org/@esbuild/linux-x64/-/linux-x64-{ESBUILD_VERSION}.tgz"
        " | tar xz -C /usr/local/bin --strip-components=2 package/bin/esbuild",
        "celld --version && esbuild --version",
    )
    .add_local_dir(HERE / "worker", PROJECT)
)

app = modal.App("celld-example", image=image)

# celld keeps everything it needs to restart -- the deployment, the object
# store, each cell's SQLite file -- in PROJECT/.celld/dev.
state = modal.Volume.from_name("celld-example-state", create_if_missing=True)


@app.function(
    volumes={f"{PROJECT}/.celld": state},
    # One node owns the state directory. A second container would be a second
    # node on the same files, so never scale past one.
    max_containers=1,
    scaledown_window=300,
    timeout=24 * 60 * 60,
)
@modal.concurrent(max_inputs=200)
@modal.web_server(PORT, startup_timeout=120)
def serve():
    subprocess.Popen(
        ["celld", "dev", PROJECT, "--host", "0.0.0.0", "--port", str(PORT), "--no-watch"]
    )
