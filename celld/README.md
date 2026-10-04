# celld on Modal

[celld](https://celld.dev) is Deno's self-hosted Cloudflare Workers and
Durable Objects runtime. This runs a small Worker on it as a Modal web
endpoint: a guestbook where every room is its own Durable Object (a celld
"cell") with its own SQLite database.

    modal deploy celld/app.py

Then open the printed URL, or:

    curl -X POST $URL/rooms/lobby -d '{"text":"hello"}'
    curl $URL/rooms/lobby

## Layout

| file | what |
|---|---|
| `worker/wrangler.jsonc` | an ordinary Wrangler config: one `Room` Durable Object class |
| `worker/index.js` | the Worker: `/` serves a page, `/rooms/NAME` routes to that room's object and returns its global id and messages |
| `app.py` | the Modal app: installs celld and esbuild, runs one celld node in fleet mode behind `@modal.web_server`, with the `minio` app as its bucket |

The Worker knows nothing about Modal. `celld dev worker` runs it unchanged on
a laptop, and `wrangler deploy` would run it on Cloudflare.

## How it runs

One container runs one celld node in fleet mode against an S3 bucket,
`celld-example`, on the `minio` app in this workspace
(`https://codegod100--minio-web.modal.run`, credentials from the
`minio-root` Secret). On start, `app.py` creates the bucket if it is missing,
runs `celld deploy` to publish the Worker to it, and starts
`celld --bucket s3://celld-example --endpoint ...`.

celld keeps each cell's SQLite file on local disk only as a cache. It ships
every committed write to the bucket as LTX before the Worker sees the write
succeed, so nothing is lost when a container stops or crashes: the next node
restores each room from the bucket the first time it is touched. There is no
Volume and no snapshot loop.

The cost is latency. A single node proves each write through the bucket, so
a write waits on a round trip to MinIO through its public URL: about 0.4 to
0.9 s here. A room's first request after a restart takes a few more seconds
while the node takes over its lease and restores it.

Verified on Modal: `celld diagnose` passes the conditional-write probe
against MinIO (create, reject-create, update, reject-stale), and rooms written
before a `kill -9` with the local state wiped, and before a redeploy onto a
fresh container, came back intact.

## Going past one node

A second celld node would
cut write latency a lot, since a write then finishes once a peer holds it
instead of waiting on the bucket. On Modal that needs private networking
between containers (`i6pn=True`) and `--advertise` on each node's internal
listener. This example stops at one node (`max_containers=1`).
