"""Shopping list tools."""

from typing import Annotated

from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from security import MAX_IDS, tool_error, validate_id, validate_ids
from tools.common import (
    DESTRUCTIVE,
    DESTRUCTIVE_IDEMPOTENT,
    MAX_NAME_LENGTH,
    READ_ONLY,
    WRITE,
    WRITE_IDEMPOTENT,
    IdList,
    item_names,
    one_line,
    require_confirm,
    service,
    tool,
    validate_recipe_ids,
)


class ItemRename(BaseModel):
    id: str = Field(description="Item ID from get_shopping_list")
    name: str = Field(description="New name", min_length=1, max_length=MAX_NAME_LENGTH)


def _tick(owned: bool) -> str:
    return "[x]" if owned else "[ ]"


@tool(READ_ONLY)
async def get_shopping_list() -> str:
    """
    Show the shopping list: the recipes on it, their ingredients, and items added by hand.

    [x] marks items already ticked off (owned). IDs are for mark_shopping_list_items,
    edit_shopping_list_items and remove_shopping_list_items.
    """
    try:
        recipes, ingredients, additional = await service().get_shopping_list()
    except Exception as e:
        raise tool_error("Failed to get the shopping list", e) from e

    if not (recipes or ingredients or additional):
        return "The shopping list is empty."
    lines = [f"Recipes ({len(recipes)}):"]
    lines += [f"  - {one_line(r.name)} | ID: {r.id}" for r in recipes] or ["  (none)"]
    lines += ["", f"Ingredients ({len(ingredients)}):"]
    for item in ingredients:
        text = " ".join(p for p in (one_line(item.description), one_line(item.name)) if p)
        lines.append(f"  {_tick(item.is_owned)} {text} | ID: {item.id}")
    if not ingredients:
        lines.append("  (none)")
    lines += ["", f"Other items ({len(additional)}):"]
    lines += [f"  {_tick(i.is_owned)} {one_line(i.name)} | ID: {i.id}" for i in additional] or [
        "  (none)"
    ]
    return "\n".join(lines)


@tool(WRITE)
async def add_recipes_to_shopping_list(
    recipe_ids: IdList | None = None,
    custom_recipe_ids: IdList | None = None,
) -> str:
    """
    Add the ingredients of recipes to the shopping list.

    Args:
        recipe_ids: Cookidoo catalogue recipe IDs (e.g. "r59322")
        custom_recipe_ids: IDs of the user's own custom recipes
    """
    official, custom = validate_recipe_ids(recipe_ids, custom_recipe_ids)
    try:
        items = await service().add_recipes_to_shopping_list(official, custom)
    except Exception as e:
        raise tool_error("Failed to add recipes to the shopping list", e) from e
    return (
        f"Added {len(official) + len(custom)} recipe(s) to the shopping list "
        f"({len(items)} ingredient items)."
    )


@tool(DESTRUCTIVE)
async def remove_recipes_from_shopping_list(
    recipe_ids: IdList | None = None,
    custom_recipe_ids: IdList | None = None,
) -> str:
    """
    Remove recipes and all their ingredient items from the shopping list, including the
    ticked state of those items. Items added by hand are kept.

    Args:
        recipe_ids: Cookidoo catalogue recipe IDs
        custom_recipe_ids: IDs of the user's own custom recipes
    """
    official, custom = validate_recipe_ids(recipe_ids, custom_recipe_ids)
    try:
        await service().remove_recipes_from_shopping_list(official, custom)
    except Exception as e:
        raise tool_error("Failed to remove recipes from the shopping list", e) from e
    return f"Removed {len(official) + len(custom)} recipe(s) from the shopping list."


@tool(WRITE)
async def add_shopping_list_items(items: list[str] | str) -> str:
    """
    Add items by hand to the shopping list (e.g. "milk", "2 lemons").

    Args:
        items: Item names, as a list (or one per line), at most 50
    """
    names = item_names(items)
    try:
        added = await service().add_shopping_list_items(names)
    except Exception as e:
        raise tool_error("Failed to add items to the shopping list", e) from e
    return "Added:\n" + "\n".join(f"  - {one_line(i.name)} | ID: {i.id}" for i in added)


@tool(DESTRUCTIVE_IDEMPOTENT)
async def edit_shopping_list_items(
    items: Annotated[list[ItemRename], Field(max_length=MAX_IDS)],
) -> str:
    """
    Rename items that were added by hand (not recipe ingredients). The old name is replaced.

    Args:
        items: List of {"id": ..., "name": ...} with IDs from get_shopping_list
    """
    if not items:
        raise ToolError("Provide at least one item to rename.")
    names_by_id = {validate_id(i.id, "item ID"): one_line(i.name) for i in items}
    try:
        renamed = await service().rename_shopping_list_items(names_by_id)
    except KeyError as e:
        raise ToolError(
            f"Not items added by hand: {', '.join(e.args[0])}. Recipe ingredients cannot be "
            "renamed; check the IDs with get_shopping_list."
        ) from e
    except Exception as e:
        raise tool_error("Failed to rename shopping list items", e) from e
    return "Renamed:\n" + "\n".join(f"  - {one_line(i.name)} | ID: {i.id}" for i in renamed)


@tool(WRITE_IDEMPOTENT)
async def mark_shopping_list_items(item_ids: IdList, owned: bool = True) -> str:
    """
    Tick items off the shopping list (owned=true), or untick them (owned=false).
    Works for recipe ingredients and for items added by hand.

    Args:
        item_ids: Item IDs from get_shopping_list
        owned: true to tick off, false to put back on the list
    """
    ids = validate_ids(item_ids, "item ID")
    if not ids:
        raise ToolError("Provide at least one item ID.")
    try:
        changed = await service().mark_shopping_list_items(ids, owned)
    except KeyError as e:
        raise ToolError(
            f"Not on the shopping list: {', '.join(e.args[0])}. Check the IDs with "
            "get_shopping_list."
        ) from e
    except Exception as e:
        raise tool_error("Failed to update shopping list items", e) from e
    return f"{'Ticked off' if owned else 'Unticked'} {len(changed)} item(s)."


@tool(DESTRUCTIVE)
async def remove_shopping_list_items(item_ids: IdList) -> str:
    """
    Delete items that were added by hand from the shopping list. Recipe ingredients are
    removed with remove_recipes_from_shopping_list instead.

    Args:
        item_ids: IDs of items added by hand, from get_shopping_list
    """
    ids = validate_ids(item_ids, "item ID")
    if not ids:
        raise ToolError("Provide at least one item ID.")
    try:
        await service().remove_shopping_list_items(ids)
    except Exception as e:
        raise tool_error("Failed to remove shopping list items", e) from e
    return f"Removed {len(ids)} item(s) from the shopping list."


@tool(DESTRUCTIVE)
async def clear_shopping_list(confirm: bool = False) -> str:
    """
    Empty the whole shopping list: every recipe, ingredient and item added by hand.
    This cannot be undone. Requires confirm=true after the user explicitly agreed.

    Args:
        confirm: Must be true; refuses otherwise
    """
    require_confirm(confirm, "delete every recipe and item on the shopping list")
    try:
        await service().clear_shopping_list()
    except Exception as e:
        raise tool_error("Failed to clear the shopping list", e) from e
    return "The shopping list is now empty."
