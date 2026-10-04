"""MkDocs hook: render the MCP tool tables from the server code at build time.

`<!-- tools:read -->` and `<!-- tools:write -->` in a page become tables built
from READ_TOOLS / WRITE_TOOLS in mcp/modal_mcp/server.py and each tool's
signature and docstring in mcp/modal_mcp/tools.py, so the reference cannot
drift from the code. Parsed with ast, so building the docs needs no modal install.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "mcp" / "modal_mcp"


def _tool_lists() -> dict[str, list[str]]:
    lists = {}
    for node in ast.parse((PKG / "server.py").read_text()).body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.List):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ("READ_TOOLS", "WRITE_TOOLS"):
                    lists[target.id] = [e.attr for e in node.value.elts]
    return lists


def _tools() -> dict[str, tuple[list[str], str]]:
    found = {}
    for node in ast.parse((PKG / "tools.py").read_text()).body:
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            params = [a.arg for a in node.args.args + node.args.kwonlyargs]
            summary = (ast.get_docstring(node) or "").split("\n\n")[0].replace("\n", " ")
            found[node.name] = (params, summary.replace("|", "\\|"))
    return found


def _table(names: list[str], tools: dict) -> str:
    rows = ["| Tool | Parameters | What it does |", "| --- | --- | --- |"]
    for name in names:
        params, summary = tools[name]
        shown = ", ".join(f"`{p}`" for p in params) or "none"
        rows.append(f"| `{name}` | {shown} | {summary} |")
    return "\n".join(rows)


def on_page_markdown(markdown, page, config, files):
    if "<!-- tools:" not in markdown:
        return markdown
    lists, tools = _tool_lists(), _tools()
    read, write = lists["READ_TOOLS"], lists["WRITE_TOOLS"]
    return (
        markdown.replace("<!-- tools:read -->", _table(read, tools))
        .replace("<!-- tools:write -->", _table(write, tools))
        .replace("<!-- tools:count -->", str(len(read) + len(write)))
        .replace("<!-- tools:read-count -->", str(len(read)))
        .replace("<!-- tools:write-count -->", str(len(write)))
    )
