# celld on Modal

[celld](https://celld.dev) is Deno's self-hosted Cloudflare Workers and
Durable Objects runtime. This runs a small Worker on it as a Modal web
endpoint: a guestbook where every room is its own Durable Object (a celld
"cell") with its own SQLite database.

    modal deploy celld/app.py

Then open the printed URL, or:

    curl -X POST https://<workspace>--celld-example-serve.modal.run/rooms/lobby \
      -d '{"text":"hello"}'
    curl https://<workspace>--celld-example-serve.modal.run/rooms/lobby

## Layout

| file | what |
|---|---|
| `worker/wrangler.jsonc` | an ordinary Wrangler config: one `Room` Durable Object class |
| `worker/index.js` | the Worker: `/` serves a page, `/rooms/NAME` routes to that room's object |
| `app.py` | the Modal app: installs celld and esbuild, runs `celld dev` behind `@modal.web_server` |

The Worker knows nothing about Modal. `celld dev worker` runs it unchanged on
a laptop, and `wrangler deploy` would run it on Cloudflare.

## How it runs

One container runs one celld node in `celld dev` mode, which keeps its object
store and every cell's SQLite file under the project's `.celld/dev`. On Modal that
directory is the `celld-example-state` Volume, so rooms survive a container
restart. `max_containers=1` is deliberate: two containers would be two nodes
writing the same files.

Verified on Modal: celld 0.6.1 runs under gVisor as-is (no shim, no VM), the
image recipe in `app.py` builds, and a restarted node reads its cells back from
its state directory on local disk. That directory living on a Volume is the one
part not yet exercised.

## Going past one node

`celld dev` is a single node with a local object store. A real celld fleet
shares an S3, GCS or Azure bucket instead, and every node runs
`celld --bucket s3://... --listen 0.0.0.0:8080 --internal-listen ... --advertise ...`
after one `celld deploy . --bucket s3://...`. On Modal that would mean a
bucket secret and private networking between containers (`i6pn=True`) for the
internal listener. This example stops short of that.
