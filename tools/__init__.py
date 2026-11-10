"""Tool groups beyond the core recipe tools in server.py."""

from fastmcp import FastMCP

from tools import (  # noqa: F401 (importing registers the tools)
    account,
    custom_recipes,
    meal_plan,
    recipe_collections,
    shopping_list,
)
from tools.common import _registry


def register(mcp: FastMCP) -> None:
    """Register every tool declared with @tool in the tool modules."""
    for fn, annotations in _registry:
        mcp.tool(annotations=annotations)(fn)
