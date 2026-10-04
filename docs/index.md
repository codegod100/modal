# modal

Tools for working with [Modal](https://modal.com): an MCP server that gives
agents caller-scoped access to a Modal workspace, a convention for defining
containers as a single `container.toml`, and an Arch Linux + nix image along
with the workarounds nix needs under Modal's gVisor runtime.

<div class="grid cards" markdown>

- **[MCP server](mcp/index.md)**

    Apps, functions, logs, sandboxes, storage, billing and HTTP service
    deployment through the public Modal Python SDK, hosted on Modal itself.

- **[Containers](containers/index.md)**

    One directory per container, defined by `container.toml`, scaffolded and
    deployed with two scripts.

- **[Nix on Modal](nix.md)**

    Why nix cannot build derivations under gVisor, and the `ptyshim.c`
    workaround.

</div>

## Quick links

| What | Where |
| --- | --- |
| Hosted MCP endpoint | `https://codegod100--modal-mcp.modal.run/mcp` |
| Source | [codegod100/modal](https://github.com/codegod100/modal) |
| MCP server code | [`mcp/`](https://github.com/codegod100/modal/tree/main/mcp) |
| Container spec | [container.toml reference](containers/spec.md) |

## Working on these docs

The site is built with [MkDocs Material](https://squidfunk.github.io/mkdocs-material/)
and deployed to GitHub Pages on every push to `main`.

```bash
pixi run docs         # live preview at http://127.0.0.1:8000
pixi run docs-build   # strict build into site/
```
