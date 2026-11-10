"""Read-only live tests: nothing on the test account is changed."""

import uuid
from dataclasses import replace
from datetime import date

import pytest
from fastmcp.exceptions import ToolError

from cookidoo_service import CookidooService
from devices import MODES
from tests.live.conftest import LiveService, call

pytestmark = [pytest.mark.live, pytest.mark.asyncio(loop_scope="session")]


async def test_connect(live: LiveService):
    text = await call(live, "connect_to_cookidoo")
    assert "cookidoo." in text


async def test_account_and_devices(live: LiveService):
    account = await call(live, "get_account_info")
    for line in ("User:", "Site: https://cookidoo.", "Subscription", "Devices:", "Device for"):
        assert line in account, f"get_account_info has no '{line}' line"
    devices = await call(live, "list_devices")
    assert devices.startswith(("- TM", "No Thermomix"))


async def test_search_and_details(live: LiveService, catalog_recipe: str):
    found = await call(live, "search_recipes", query="Risotto", max_results=3)
    assert "ID:" in found
    by_ingredient = await call(live, "search_recipes", ingredients="Reis, Zwiebel", max_results=3)
    assert "ID:" in by_ingredient or "No recipes" in by_ingredient
    details = await call(live, "get_recipe_details", recipe_id=catalog_recipe)
    assert catalog_recipe in details or "Ingredients" in details or "Zutaten" in details


async def test_unknown_recipe_is_a_clean_error(live: LiveService):
    with pytest.raises(ToolError):
        await call(live, "get_recipe_details", recipe_id="r0000000000")


async def test_lists(live: LiveService):
    assert await call(live, "list_custom_recipes")
    shopping = await call(live, "get_shopping_list")
    assert shopping.startswith(("Recipes (", "The shopping list is empty"))
    week = await call(live, "get_meal_plan_week", day=date.today().isoformat())
    assert week.startswith(("Meal plan from", "Nothing planned from"))
    collections = await call(live, "list_collections")
    assert "My collections (" in collections and "Followed collections (" in collections


async def test_thermomix_modes(live: LiveService):
    for device in ("TM5", "TM6", "TM7"):
        text = await call(live, "get_thermomix_modes", device=device)
        assert text.startswith(f"Thermomix {device} (device: given; modes: ")
    catalog = await live.get_mode_catalog()
    assert set(catalog.modes) <= set(MODES)


async def test_wrong_password_is_refused_without_echo(live: LiveService):
    wrong = replace(live.settings, password=f"not-the-password-{uuid.uuid4().hex}")
    svc = CookidooService(wrong)
    try:
        with pytest.raises(Exception) as error:
            await svc.login()
    finally:
        await svc.close()
    assert wrong.password not in str(error.value)
    assert live.settings.password not in str(error.value)
