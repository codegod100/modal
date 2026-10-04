"""celld on Modal: a Workers + Durable Objects app served from one container.

    modal serve celld/app.py     # dev URL, hot reload of this file
    modal deploy celld/app.py    # stable URL

celld (https://celld.dev) runs a Wrangler project -- here `worker/` -- with
each Durable Object as a "cell" that owns its own SQLite database. This runs
one celld node in fleet mode against an S3-compatible bucket: the `minio` app
in this workspace.

celld keeps each cell's SQLite file on local disk only as a cache. Every
committed write is shipped to the bucket as LTX before the Worker sees it
succeed, so a fresh container (or a crashed one) restores every room from the
bucket. Nothing needs to be copied to a Volume.
"""

from pathlib import Path
import os
import signal
import subprocess

import modal

CELLD_VERSION = "v0.6.1"
ESBUILD_VERSION = "0.28.2"  # `celld deploy` bundles the Worker with esbuild
PORT = 9876
INTERNAL = "127.0.0.1:9877"  # peer/operator listener; one node, so loopback
PROJECT = "/app"
BUCKET = "celld-example"
# The `minio` app's public S3 endpoint. Its root credentials come from the
# `minio-root` Secret (MINIO_ROOT_USER / MINIO_ROOT_PASSWORD).
ENDPOINT = "https://codegod100--minio-web.modal.run"
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
    .pip_install("boto3")
    .add_local_dir(HERE / "worker", PROJECT)
)

app = modal.App("celld-example", image=image)


@app.cls(
    secrets=[modal.Secret.from_name("minio-root")],
    # celld fences ownership through conditional writes to the bucket, so an
    # overlapping container during a redeploy is safe. More than one node
    # would also need peer networking between containers, so stay at one.
    max_containers=1,
    scaledown_window=300,
    timeout=24 * 60 * 60,
)
@modal.concurrent(max_inputs=200)
class Celld:
    @modal.enter()
    def start(self):
        env = {
            **os.environ,
            "AWS_ACCESS_KEY_ID": os.environ["MINIO_ROOT_USER"],
            "AWS_SECRET_ACCESS_KEY": os.environ["MINIO_ROOT_PASSWORD"],
            "AWS_REGION": "us-east-1",
        }
        _ensure_bucket(env)
        fleet = ["--bucket", f"s3://{BUCKET}", "--endpoint", ENDPOINT]
        # Publish the Worker to the bucket; a running node adopts it too.
        subprocess.run(["celld", "deploy", PROJECT, *fleet], env=env, check=True)
        self.proc = subprocess.Popen(
            ["celld", *fleet, "--listen", f"0.0.0.0:{PORT}", "--internal-listen", INTERNAL],
            env=env,
        )

    @modal.web_server(PORT, startup_timeout=120)
    def serve(self):
        pass

    @modal.exit()
    def stop(self):
        self.proc.send_signal(signal.SIGINT)
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def _ensure_bucket(env):
    import boto3

    s3 = boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        aws_access_key_id=env["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=env["AWS_SECRET_ACCESS_KEY"],
        region_name=env["AWS_REGION"],
    )
    if BUCKET not in {b["Name"] for b in s3.list_buckets()["Buckets"]}:
        s3.create_bucket(Bucket=BUCKET)
