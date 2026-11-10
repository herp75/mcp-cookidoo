"""
Shared helpers for the tool modules: annotations, registration, validation and formatting.

Tool modules declare tools with @tool(...) and are registered on the server by
tools.register(mcp). They import the server lazily (service()) so that tests can swap the
service on the server module, exactly as for the tools defined in server.py.
"""

import re
from collections.abc import Callable
from typing import Annotated, Any

from fastmcp.exceptions import ToolError
from pydantic import Field

from security import MAX_IDS, validate_ids

READ_ONLY = {"readOnlyHint": True, "openWorldHint": True}
WRITE = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}
WRITE_IDEMPOTENT = {**WRITE, "idempotentHint": True}
DESTRUCTIVE = {**WRITE, "destructiveHint": True}
DESTRUCTIVE_IDEMPOTENT = {**DESTRUCTIVE, "idempotentHint": True}

MAX_NAME_LENGTH = 200

# Optional list of IDs, as accepted by tools that take official and/or custom recipe IDs
IdList = Annotated[list[str], Field(max_length=MAX_IDS)]
Page = Annotated[int, Field(ge=0, le=1000, description="Page number, starting at 0")]

_registry: list[tuple[Callable[..., Any], dict]] = []


def tool(annotations: dict) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Mark a function as an MCP tool, to be registered by tools.register()."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        _registry.append((fn, annotations))
        return fn

    return decorator


def service():
    """The server's lazily created CookidooService (imported late: server imports us)."""
    import server

    return server._get_service()


def require_confirm(confirm: bool, what: str) -> None:
    """Second safety layer for irreversible tools, on top of the client's approval prompt."""
    if confirm is not True:
        raise ToolError(
            f"Not done: this would {what}, which cannot be undone. Ask the user to confirm, "
            "then call again with confirm=true."
        )


def validate_recipe_ids(
    ids: list[str] | None, custom_ids: list[str] | None, *, required: bool = True
) -> tuple[list[str], list[str]]:
    """Validate official and custom recipe ID lists (max MAX_IDS in total)."""
    official = validate_ids(ids or [])
    custom = validate_ids(custom_ids or [], "custom recipe ID")
    if required and not (official or custom):
        raise ToolError("Provide at least one ID in recipe_ids or custom_recipe_ids.")
    if len(official) + len(custom) > MAX_IDS:
        raise ToolError(f"Too many recipes: at most {MAX_IDS} per call.")
    return official, custom


def item_names(value: list[str] | str) -> list[str]:
    """Free-text item names: one per list entry or line, single-line, length-capped."""
    names = value.split("\n") if isinstance(value, str) else value
    names = [one_line(name) for name in names if name.strip()]
    if not names:
        raise ToolError("Provide at least one item name.")
    if len(names) > MAX_IDS:
        raise ToolError(f"Too many items: at most {MAX_IDS} per call.")
    if too_long := [n for n in names if len(n) > MAX_NAME_LENGTH]:
        raise ToolError(f"Item names are limited to {MAX_NAME_LENGTH} characters: {too_long[0]!r}")
    return names


def one_line(text: object) -> str:
    """
    Collapse a value from Cookidoo (or another user) onto one line.

    Names and descriptions are untrusted: keeping each on its own labelled line stops them from
    faking extra fields or tool output structure.
    """
    return re.sub(r"\s+", " ", str(text or "")).strip()


def more_pages(page: int, pages: int) -> str:
    if page + 1 < pages:
        return f"Page {page + 1} of {pages}. More results: call again with page={page + 1}."
    return f"Page {page + 1} of {max(pages, 1)}."
