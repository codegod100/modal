"""MinIO on Modal: an S3-compatible object store served from one container.

    modal deploy minio/app.py    # https://codegod100--minio-web.modal.run

This is the source of the `minio` app in this workspace, which was first
deployed through the MCP's `deploy_web_function` tool (see mcp/README.md).
It keeps that deployment's shape -- app `minio`, function `web` -- so the URL,
the data and the credentials carry over when it is deployed from here.

Objects live on the `minio-data` Volume. The root credentials come from the
`minio-root` Secret (MINIO_ROOT_USER / MINIO_ROOT_PASSWORD), which MinIO reads
from its environment.
"""

import subprocess

import modal

# Built from source at a pinned release tag, as the original deployment was.
MINIO_RELEASE = "RELEASE.2025-10-15T17-29-55Z"
GO_VERSION = "1.24.8"
PORT = 9000
DATA = "/data"

image = (
    modal.Image.from_registry("debian:bookworm-slim", add_python="3.12")
    .run_commands(
        "apt-get update && apt-get install -y curl git ca-certificates",
        f"curl -fsSL https://go.dev/dl/go{GO_VERSION}.linux-amd64.tar.gz | tar -C /usr/local -xz"
        f" && git clone -q --depth 1 -b {MINIO_RELEASE} https://github.com/minio/minio /src"
        " && cd /src && CGO_ENABLED=0 /usr/local/go/bin/go build -trimpath -o /usr/local/bin/minio ."
        " && rm -rf /src /usr/local/go /root/go /root/.cache",
    )
)

app = modal.App("minio", image=image)


@app.function(
    cpu=1.0,
    memory=512,
    # MinIO owns its data directory; two servers on one Volume would corrupt it.
    max_containers=1,
    volumes={DATA: modal.Volume.from_name("minio-data", create_if_missing=True)},
    secrets=[modal.Secret.from_name("minio-root")],
)
# Public: clients authenticate with S3 signatures, not Modal proxy tokens.
@modal.web_server(PORT, startup_timeout=60, requires_proxy_auth=False)
def web():
    subprocess.Popen(["minio", "server", DATA, "--address", f"0.0.0.0:{PORT}"])
