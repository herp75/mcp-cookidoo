"""Meal plan (Cookidoo calendar) tools."""

from datetime import date, timedelta

from fastmcp.exceptions import ToolError

from cookidoo_service import MoveError
from security import redact, tool_error, validate_id
from tools.common import (
    DESTRUCTIVE,
    READ_ONLY,
    WRITE,
    IdList,
    one_line,
    service,
    tool,
    validate_recipe_ids,
)


@tool(READ_ONLY)
async def get_meal_plan_week(day: date | None = None) -> str:
    """
    Show the meal plan for the 7 days starting at a day (default: today).

    Args:
        day: The first day, as YYYY-MM-DD
    """
    day = day or date.today()
    span = f"{day.isoformat()} to {(day + timedelta(days=6)).isoformat()}"
    try:
        days = await service().get_meal_plan_week(day)
    except Exception as e:
        raise tool_error("Failed to get the meal plan", e) from e

    planned = [d for d in days if d.recipes or d.customer_recipe_ids]
    if not planned:
        return f"Nothing planned from {span}."
    lines = [f"Meal plan from {span}:"]
    for d in planned:
        lines += ["", f"{one_line(d.id)} ({one_line(d.title)}):"]
        for r in d.recipes:
            kind = "custom recipe" if r.is_custom else "recipe"
            lines.append(f"  - {one_line(r.name)} | {kind} ID: {r.id}")
        resolved = {r.id for r in d.recipes}
        for recipe_id in d.customer_recipe_ids:
            if recipe_id not in resolved:
                lines.append(f"  - (custom recipe, not found) | custom recipe ID: {recipe_id}")
    return "\n".join(lines)


@tool(WRITE)
async def add_to_meal_plan(
    day: date,
    recipe_ids: IdList | None = None,
    custom_recipe_ids: IdList | None = None,
) -> str:
    """
    Plan recipes on a day of the meal plan.

    Args:
        day: The day, as YYYY-MM-DD
        recipe_ids: Cookidoo catalogue recipe IDs
        custom_recipe_ids: IDs of the user's own custom recipes
    """
    official, custom = validate_recipe_ids(recipe_ids, custom_recipe_ids)
    try:
        await service().add_to_meal_plan(day, official, custom)
    except Exception as e:
        raise tool_error("Failed to add to the meal plan", e) from e
    return f"Planned {len(official) + len(custom)} recipe(s) on {day.isoformat()}."


@tool(DESTRUCTIVE)
async def remove_from_meal_plan(
    day: date,
    recipe_ids: IdList | None = None,
    custom_recipe_ids: IdList | None = None,
) -> str:
    """
    Remove recipes from one day of the meal plan. The recipes themselves are not deleted.

    Args:
        day: The day, as YYYY-MM-DD
        recipe_ids: Cookidoo catalogue recipe IDs planned on that day
        custom_recipe_ids: IDs of the user's own custom recipes planned on that day
    """
    official, custom = validate_recipe_ids(recipe_ids, custom_recipe_ids)
    try:
        await service().remove_from_meal_plan(day, official, custom)
    except Exception as e:
        raise tool_error("Failed to remove from the meal plan", e) from e
    return f"Removed {len(official) + len(custom)} recipe(s) from {day.isoformat()}."


@tool(DESTRUCTIVE)
async def move_in_meal_plan(recipe_id: str, from_day: date, to_day: date) -> str:
    """
    Move a planned recipe (catalogue or custom) from one day of the meal plan to another.
    It is added to the new day before it is removed from the old one; if that fails, the
    change is rolled back.

    Args:
        recipe_id: ID of a recipe planned on from_day (from get_meal_plan_week)
        from_day: The day it is planned on, as YYYY-MM-DD
        to_day: The new day, as YYYY-MM-DD
    """
    recipe_id = validate_id(recipe_id)
    if from_day == to_day:
        return f"Nothing to do: {recipe_id} is already planned on {to_day.isoformat()}."
    try:
        is_custom = await service().move_in_meal_plan(recipe_id, from_day, to_day)
    except LookupError as e:
        raise ToolError(
            f"{recipe_id} is not planned on {from_day.isoformat()}: check get_meal_plan_week."
        ) from e
    except MoveError as e:
        if e.rolled_back:
            raise ToolError(
                redact(
                    f"Move failed, nothing changed: {recipe_id} is still planned on "
                    f"{from_day.isoformat()} only. Cause: {e.error}"
                )
            ) from e
        raise ToolError(
            redact(
                f"Move failed and could not be rolled back: {recipe_id} is now planned on both "
                f"{from_day.isoformat()} and {to_day.isoformat()}. Remove it from one day with "
                f"remove_from_meal_plan. Cause: {e.error}; rollback: {e.rollback_error}"
            )
        ) from e
    except Exception as e:
        raise tool_error("Move failed, nothing changed", e) from e
    kind = "custom recipe" if is_custom else "recipe"
    return f"Moved {kind} {recipe_id} from {from_day.isoformat()} to {to_day.isoformat()}."
