// A guestbook per room. Every room name is its own Durable Object -- a celld
// "cell" -- with its own SQLite database.
export class Room {
  constructor(ctx, env) {
    // The global id: derived from the room name, the same on every node.
    this.id = ctx.id.toString();
    this.sql = ctx.storage.sql;
    this.sql.exec(`CREATE TABLE IF NOT EXISTS messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      text TEXT NOT NULL,
      at TEXT NOT NULL DEFAULT (datetime('now'))
    )`);
  }

  async fetch(request) {
    if (request.method === "POST") {
      const { text } = await request.json();
      if (typeof text !== "string" || !text.trim()) {
        return Response.json({ error: "text is required" }, { status: 400 });
      }
      this.sql.exec("INSERT INTO messages (text) VALUES (?)", text.slice(0, 500));
    }
    const messages = this.sql
      .exec("SELECT id, text, at FROM messages ORDER BY id DESC LIMIT 50")
      .toArray();
    return Response.json({ id: this.id, messages });
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const match = url.pathname.match(/^\/rooms\/([\w-]{1,64})$/);
    if (match) {
      return env.ROOM.getByName(match[1]).fetch(request);
    }
    if (url.pathname === "/") {
      return new Response(PAGE, { headers: { "content-type": "text/html; charset=utf-8" } });
    }
    return new Response("not found", { status: 404 });
  },
};

const PAGE = `<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>celld on Modal</title>
<style>
  body { font: 16px/1.5 system-ui, sans-serif; max-width: 36rem; margin: 2rem auto; padding: 0 1rem; }
  input, button { font: inherit; padding: .4rem .6rem; }
  li { margin: .25rem 0; } small { color: #777; }
  code { font-size: .8em; word-break: break-all; }
</style>
<h1>celld on Modal</h1>
<p>Each room is a Durable Object with its own SQLite database.</p>
<p><label>Room <input id="room" value="lobby"></label></p>
<p>Durable Object id <code id="id"></code></p>
<form id="form"><input id="text" placeholder="Say something" required> <button>Post</button></form>
<ul id="list"></ul>
<script>
  const $ = (id) => document.getElementById(id);
  async function load(init) {
    const res = await fetch("/rooms/" + encodeURIComponent($("room").value), init);
    const { id = "", messages = [] } = await res.json();
    $("id").textContent = id;
    $("list").replaceChildren(...messages.map((m) => {
      const li = document.createElement("li");
      li.textContent = m.text + " ";
      const at = document.createElement("small");
      at.textContent = m.at;
      li.append(at);
      return li;
    }));
  }
  $("form").onsubmit = async (e) => {
    e.preventDefault();
    await load({ method: "POST", body: JSON.stringify({ text: $("text").value }) });
    $("text").value = "";
  };
  $("room").onchange = () => load();
  load();
</script>`;
