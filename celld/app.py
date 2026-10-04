"""celld on Modal: a Workers + Durable Objects app served from one container.

    modal serve celld/app.py     # dev URL, hot reload of this file
    modal deploy celld/app.py    # stable URL

celld (https://celld.dev) runs a Wrangler project -- here `worker/` -- with
each Durable Object as a "cell" that owns its own SQLite database. This runs
one node in `celld dev` mode.

A single node acknowledges a write only once it is in the node's object store,
`.celld/dev/objects.sqlite3`, and that one file is enough to bring every cell
back. So celld runs on local disk, and the store is copied to a Modal Volume:
restored when a container starts, snapshotted every few seconds while it runs,
and once more when it stops.
"""

from pathlib import Path
import os
import shutil
import signal
import sqlite3
import subprocess
import threading

import modal

CELLD_VERSION = "v0.6.1"
ESBUILD_VERSION = "0.28.2"  # `celld dev` bundles the Worker with esbuild
PORT = 9876
PROJECT = "/app"
STORE = f"{PROJECT}/.celld/dev/objects.sqlite3"
SNAPSHOT = "/state/objects.sqlite3"
SNAPSHOT_EVERY_S = 5
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
state = modal.Volume.from_name("celld-example-state", create_if_missing=True)


@app.cls(
    volumes={"/state": state},
    # One node owns the store. A second container would be a second node
    # restoring the same snapshot and overwriting the other's, so never
    # scale past one.
    max_containers=1,
    scaledown_window=300,
    timeout=24 * 60 * 60,
)
@modal.concurrent(max_inputs=200)
class Celld:
    @modal.enter()
    def start(self):
        if os.path.exists(SNAPSHOT):
            os.makedirs(os.path.dirname(STORE), exist_ok=True)
            shutil.copyfile(SNAPSHOT, STORE)
            print(f"restored {STORE} from the volume")
        self.lock = threading.Lock()
        self.last = None
        self.proc = subprocess.Popen(
            ["celld", "dev", PROJECT, "--host", "0.0.0.0", "--port", str(PORT), "--no-watch"]
        )
        self.stopping = threading.Event()
        threading.Thread(target=self._snapshot_loop, daemon=True).start()

    @modal.web_server(PORT, startup_timeout=120)
    def serve(self):
        pass

    @modal.exit()
    def stop(self):
        self.stopping.set()
        # SIGINT is a clean shutdown: celld checkpoints its WAL first.
        self.proc.send_signal(signal.SIGINT)
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self._snapshot()

    def _snapshot_loop(self):
        while not self.stopping.wait(SNAPSHOT_EVERY_S):
            try:
                self._snapshot()
            except Exception as e:  # keep serving; the next pass retries
                print(f"snapshot failed: {e}")

    def _snapshot(self):
        with self.lock:
            if not os.path.exists(STORE):
                return
            # The store is WAL-mode SQLite, so a write can land in either file.
            stamp = tuple(
                (s.st_mtime_ns, s.st_size)
                for s in (os.stat(p) for p in (STORE, STORE + "-wal") if os.path.exists(p))
            )
            if stamp == self.last:
                return
            # The backup API copies a consistent database while celld writes,
            # to local disk first so no SQLite journal ever touches the volume.
            _copy_sqlite(STORE, "/tmp/objects.sqlite3")
            shutil.copyfile("/tmp/objects.sqlite3", SNAPSHOT + ".tmp")
            os.replace(SNAPSHOT + ".tmp", SNAPSHOT)
            state.commit()
            self.last = stamp


def _copy_sqlite(src: str, dst: str):
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
