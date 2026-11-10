"""Collection tools: followed (managed) collections and the user's own (custom) collections."""

from typing import Annotated, Literal

from cookidoo_api import CookidooCollection
from fastmcp.exceptions import ToolError
from pydantic import Field

from security import tool_error, validate_id, validate_ids
from tools.common import (
    DESTRUCTIVE,
    DESTRUCTIVE_IDEMPOTENT,
    MAX_NAME_LENGTH,
    READ_ONLY,
    WRITE,
    IdList,
    Page,
    more_pages,
    one_line,
    require_confirm,
    service,
    tool,
)


def _format(collection: CookidooCollection) -> list[str]:
    recipes = [r for chapter in collection.chapters for r in chapter.recipes]
    lines = [f"- {one_line(collection.name)} | ID: {collection.id} | {len(recipes)} recipe(s)"]
    if collection.description:
        lines.append(f"  Description: {one_line(collection.description)}")
    for chapter in collection.chapters:
        if len(collection.chapters) > 1 and chapter.name:
            lines.append(f"  {one_line(chapter.name)}:")
        lines += [f"    - {one_line(r.name)} | ID: {r.id}" for r in chapter.recipes]
    return lines


@tool(READ_ONLY)
async def list_collections(
    kind: Literal["all", "custom", "managed"] = "all", page: Page = 0
) -> str:
    """
    List recipe collections with their recipes: the user's own ("custom") and the Cookidoo
    collections they follow ("managed"). Results are paginated.

    Args:
        kind: "custom", "managed" or "all" (default)
        page: Page number, starting at 0
    """
    kinds = ["custom", "managed"] if kind == "all" else [kind]
    lines: list[str] = []
    for k in kinds:
        try:
            collections, count, pages = await service().list_collections(k == "managed", page)
        except Exception as e:
            raise tool_error(f"Failed to list {k} collections", e) from e
        title = "My collections" if k == "custom" else "Followed collections"
        lines += [f"{title} ({count}):", *(line for c in collections for line in _format(c))]
        if not collections:
            lines.append("  (none on this page)" if count else "  (none)")
        if count:
            lines.append(more_pages(page, pages))
        lines.append("")
    return "\n".join(lines).strip()


@tool(WRITE)
async def follow_collection(collection_id: str) -> str:
    """
    Follow (save) a Cookidoo collection, adding it to the user's followed collections.

    Args:
        collection_id: ID of a Cookidoo collection (e.g. from a recipe page or search)
    """
    collection_id = validate_id(collection_id, "collection ID")
    try:
        collection = await service().follow_collection(collection_id)
    except Exception as e:
        raise tool_error("Failed to follow the collection", e) from e
    return f"Now following '{one_line(collection.name)}' (ID: {collection.id})."


@tool(DESTRUCTIVE_IDEMPOTENT)
async def unfollow_collection(collection_id: str) -> str:
    """
    Stop following a Cookidoo collection. It disappears from the user's followed collections
    (it can be followed again later).

    Args:
        collection_id: ID from list_collections (kind="managed")
    """
    collection_id = validate_id(collection_id, "collection ID")
    try:
        await service().unfollow_collection(collection_id)
    except Exception as e:
        raise tool_error("Failed to unfollow the collection", e) from e
    return f"Unfollowed collection {collection_id}."


@tool(WRITE)
async def create_collection(
    name: Annotated[str, Field(min_length=1, max_length=MAX_NAME_LENGTH)],
) -> str:
    """
    Create a new, empty custom collection on the user's account. Each call creates another
    collection.

    Args:
        name: Collection name
    """
    if not one_line(name):
        raise ToolError("The collection name must not be empty.")
    try:
        collection = await service().create_collection(one_line(name))
    except Exception as e:
        raise tool_error("Failed to create the collection", e) from e
    return f"Collection '{one_line(collection.name)}' created.\nCollection ID: {collection.id}"


@tool(DESTRUCTIVE)
async def delete_collection(collection_id: str, confirm: bool = False) -> str:
    """
    Delete one of the user's custom collections. The recipes in it are not deleted, but the
    collection and its organisation are lost; this cannot be undone. Requires confirm=true
    after the user explicitly agreed.

    Args:
        collection_id: ID from list_collections (kind="custom")
        confirm: Must be true; refuses otherwise
    """
    collection_id = validate_id(collection_id, "collection ID")
    require_confirm(confirm, f"delete the collection {collection_id}")
    try:
        await service().delete_collection(collection_id)
    except Exception as e:
        raise tool_error("Failed to delete the collection", e) from e
    return f"Collection {collection_id} deleted."


@tool(WRITE)
async def add_to_collection(collection_id: str, recipe_ids: IdList) -> str:
    """
    Add Cookidoo catalogue recipes to one of the user's custom collections.

    Args:
        collection_id: ID from list_collections (kind="custom")
        recipe_ids: Cookidoo catalogue recipe IDs (e.g. "r59322")
    """
    collection_id = validate_id(collection_id, "collection ID")
    ids = validate_ids(recipe_ids)
    if not ids:
        raise ToolError("Provide at least one recipe ID.")
    try:
        collection = await service().add_to_collection(collection_id, ids)
    except Exception as e:
        raise tool_error("Failed to add recipes to the collection", e) from e
    return f"Added {len(ids)} recipe(s) to '{one_line(collection.name)}'."


@tool(DESTRUCTIVE_IDEMPOTENT)
async def remove_from_collection(collection_id: str, recipe_ids: IdList) -> str:
    """
    Remove recipes from one of the user's custom collections. The recipes themselves are
    not deleted.

    Args:
        collection_id: ID from list_collections (kind="custom")
        recipe_ids: IDs of recipes in that collection
    """
    collection_id = validate_id(collection_id, "collection ID")
    ids = validate_ids(recipe_ids)
    if not ids:
        raise ToolError("Provide at least one recipe ID.")
    try:
        await service().remove_from_collection(collection_id, ids)
    except Exception as e:
        raise tool_error("Failed to remove recipes from the collection", e) from e
    return f"Removed {len(ids)} recipe(s) from collection {collection_id}."
