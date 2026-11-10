"""Meal plan (Cookidoo calendar) tools."""

from datetime import date

from security import tool_error
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
    Show the meal plan for the whole week containing a day (default: today).
    The week boundaries are those of the Cookidoo account.

    Args:
        day: Any day of the week, as YYYY-MM-DD
    """
    day = day or date.today()
    try:
        days = await service().get_meal_plan_week(day)
    except Exception as e:
        raise tool_error("Failed to get the meal plan", e) from e

    planned = [d for d in days if d.recipes or d.customer_recipe_ids]
    if not planned:
        return f"Nothing planned in the week of {day.isoformat()}."
    lines = [f"Meal plan for the week of {day.isoformat()}:"]
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
