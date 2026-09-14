"""MCP server exposing the local Modal CLI as tools."""

__all__ = ["build_mcp"]


def __getattr__(name):
    if name in __all__:
        from . import server

        return getattr(server, name)
    raise AttributeError(name)
