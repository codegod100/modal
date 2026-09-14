"""MCP server exposing Modal.com management tools."""

__all__ = ["build_mcp", "build_asgi_app"]


def __getattr__(name):
    if name in __all__:
        from . import server

        return getattr(server, name)
    raise AttributeError(name)
