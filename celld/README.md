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
| `app.py` | the Modal app: installs celld and esbuild, runs `celld dev` behind `@modal.web_server` |

The Worker knows nothing about Modal. `celld dev worker` runs it unchanged on
a laptop, and `wrangler deploy` would run it on Cloudflare.

## How it runs

One container runs one celld node in `celld dev` mode on local disk. A single
node acknowledges a write only once it is in its object store,
`.celld/dev/objects.sqlite3`, and that one file is enough to bring every cell
back. So `app.py` keeps a copy of it on the `celld-example-state` Volume:

* restored into place when a container starts,
* snapshotted with SQLite's backup API every 5 seconds while anything changed,
* snapshotted once more after a clean shutdown when the container scales down.

A crash can lose at most the last 5 seconds. SQLite never runs on the Volume
itself; each snapshot is written to local disk and then copied over.
`max_containers=1` is deliberate: two containers would be two nodes restoring
the same snapshot and overwriting each other's.

Verified on Modal: celld 0.6.1 runs under gVisor as-is (no shim, no VM), and
the image recipe builds. The snapshot and restore code was exercised against a
real celld with the Volume stubbed out by a local directory: a kill without
shutdown, a restore, a clean stop and a second restore kept every message.

## Going past one node

`celld dev` is a single node with a local object store. A real celld fleet
shares an S3, GCS or Azure bucket instead, and every node runs
`celld --bucket s3://... --listen 0.0.0.0:8080 --internal-listen ... --advertise ...`
after one `celld deploy . --bucket s3://...`. On Modal that would mean a
bucket secret and private networking between containers (`i6pn=True`) for the
internal listener. This example stops short of that.
