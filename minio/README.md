# MinIO on Modal

An S3-compatible object store: one MinIO server behind `@modal.web_server`,
storing objects on a Modal Volume.

    modal deploy minio/app.py

It serves at `https://codegod100--minio-web.modal.run`. That endpoint is
public, and clients sign in with S3 credentials.

| piece | name |
|---|---|
| app / function | `minio` / `web` |
| data | Volume `minio-data`, mounted at `/data`; MinIO serves `/data/store` |
| credentials | Secret `minio-root` (`MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`) |
| release | `RELEASE.2025-10-15T17-29-55Z`, built from source with Go 1.24.8 |

`max_containers = 1` is deliberate: MinIO assumes it alone owns its data
directory. It serves `/data/store` rather than the mount itself, because
MinIO fails on the Volume's root with "Unable to write to the backend".

Use it from any S3 client with path-style addressing and region `us-east-1`,
for example:

    AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... \
      aws --endpoint-url https://codegod100--minio-web.modal.run s3 ls

`celld/` uses it this way as its bucket.

The app was first deployed through the MCP's `deploy_web_function` tool (the
example in `mcp/README.md`). This file deploys the same definition, so
deploying from here replaces it in place and keeps its URL and data.
