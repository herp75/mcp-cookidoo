"""
Shared test doubles.

FakeCookidoo stands in for cookidoo_api.Cookidoo: it keeps an in-memory account built from the
sanitized fixtures in tests/fixtures/, returns the library's own dataclasses, records every call,
and can be told to fail. Fixtures contain no real names, e-mail addresses or IDs.
"""

import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from cookidoo_api import CookidooRequestException, ThermomixMachineType
from cookidoo_api.types import (
    CookidooAdditionalItem,
    CookidooCalendarDay,
    CookidooCalendarDayRecipe,
    CookidooChapter,
    CookidooChapterRecipe,
    CookidooCollection,
    CookidooDevice,
    CookidooIngredientItem,
    CookidooShoppingRecipe,
    CookidooSubscription,
    CookidooUserInfo,
)
from fastmcp import Client

import server
from cookidoo_service import CookidooService, CookidooSettings

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def collection(data: dict) -> CookidooCollection:
    return CookidooCollection(
        id=data["id"],
        name=data["name"],
        description=data["description"],
        chapters=[
            CookidooChapter(
                name=c["name"], recipes=[CookidooChapterRecipe(**r) for r in c["recipes"]]
            )
            for c in data["chapters"]
        ],
    )


class FakeCookidoo:
    """In-memory Cookidoo account implementing the library methods the server uses."""

    def __init__(self) -> None:
        account, shopping = load("account"), load("shopping_list")
        collections, plan = load("collections"), load("meal_plan")
        self.calls: list[tuple[str, tuple]] = []
        self.failures: dict[str, BaseException] = {}
        self.user = CookidooUserInfo(**account["user"])
        self.subscription: CookidooSubscription | None = CookidooSubscription(
            **account["subscription"]
        )
        self.devices = [CookidooDevice(type=ThermomixMachineType(d)) for d in account["devices"]]
        self.shopping_recipes = [
            CookidooShoppingRecipe(ingredients=[], **r) for r in shopping["recipes"]
        ]
        self.ingredients = [CookidooIngredientItem(**i) for i in shopping["ingredients"]]
        self.additional = [CookidooAdditionalItem(**i) for i in shopping["additional"]]
        self.managed = [collection(c) for c in collections["managed"]]
        self.custom = [collection(c) for c in collections["custom"]]
        self.days = [
            CookidooCalendarDay(
                id=d["id"],
                title=d["title"],
                recipes=[CookidooCalendarDayRecipe(**r) for r in d["recipes"]],
                customer_recipe_ids=d["customer_recipe_ids"],
            )
            for d in plan["days"]
        ]
        self.page_size = 10
        self._next_id = 0

    def _call(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))
        if name in self.failures:
            raise self.failures[name]

    def called(self, name: str) -> list[tuple]:
        return [args for call, args in self.calls if call == name]

    def _new_id(self, prefix: str) -> str:
        self._next_id += 1
        return f"{prefix}-new-{self._next_id:04d}"

    # Account
    async def get_user_info(self):
        self._call("get_user_info")
        return self.user

    async def get_active_subscription(self):
        self._call("get_active_subscription")
        return self.subscription

    async def get_devices(self):
        self._call("get_devices")
        return self.devices

    # Custom recipes
    async def remove_custom_recipe(self, custom_recipe_id):
        self._call("remove_custom_recipe", custom_recipe_id)

    # Shopping list
    async def get_shopping_list_recipes(self):
        self._call("get_shopping_list_recipes")
        return self.shopping_recipes

    async def get_ingredient_items(self):
        self._call("get_ingredient_items")
        return self.ingredients

    async def get_additional_items(self):
        self._call("get_additional_items")
        return self.additional

    async def _add_recipe_items(self, name, recipe_ids):
        self._call(name, recipe_ids)
        items = [
            CookidooIngredientItem(
                id=self._new_id("ing"), name=f"ingredient of {r}", is_owned=False, description="1"
            )
            for r in recipe_ids
        ]
        self.ingredients += items
        return items

    async def add_ingredient_items_for_recipes(self, recipe_ids):
        return await self._add_recipe_items("add_ingredient_items_for_recipes", recipe_ids)

    async def add_ingredient_items_for_custom_recipes(self, recipe_ids):
        return await self._add_recipe_items("add_ingredient_items_for_custom_recipes", recipe_ids)

    async def remove_ingredient_items_for_recipes(self, recipe_ids):
        self._call("remove_ingredient_items_for_recipes", recipe_ids)

    async def remove_ingredient_items_for_custom_recipes(self, recipe_ids):
        self._call("remove_ingredient_items_for_custom_recipes", recipe_ids)

    async def edit_ingredient_items_ownership(self, ingredient_items):
        self._call("edit_ingredient_items_ownership", ingredient_items)
        return ingredient_items

    async def add_additional_items(self, additional_item_names):
        self._call("add_additional_items", additional_item_names)
        items = [
            CookidooAdditionalItem(id=self._new_id("add"), name=n, is_owned=False)
            for n in additional_item_names
        ]
        self.additional += items
        return items

    async def edit_additional_items(self, additional_items):
        self._call("edit_additional_items", additional_items)
        return additional_items

    async def edit_additional_items_ownership(self, additional_items):
        self._call("edit_additional_items_ownership", additional_items)
        return additional_items

    async def remove_additional_items(self, additional_item_ids):
        self._call("remove_additional_items", additional_item_ids)

    async def clear_shopping_list(self):
        self._call("clear_shopping_list")
        self.shopping_recipes, self.ingredients, self.additional = [], [], []

    # Collections
    def _page(self, items, page):
        return items[page * self.page_size : (page + 1) * self.page_size]

    def _count(self, items):
        return len(items), -(-len(items) // self.page_size)

    async def count_managed_collections(self):
        self._call("count_managed_collections")
        return self._count(self.managed)

    async def get_managed_collections(self, page=0):
        self._call("get_managed_collections", page)
        return self._page(self.managed, page)

    async def count_custom_collections(self):
        self._call("count_custom_collections")
        return self._count(self.custom)

    async def get_custom_collections(self, page=0):
        self._call("get_custom_collections", page)
        return self._page(self.custom, page)

    async def add_managed_collection(self, managed_collection_id):
        self._call("add_managed_collection", managed_collection_id)
        return CookidooCollection(
            id=managed_collection_id, name="Test Followed", description=None, chapters=[]
        )

    async def remove_managed_collection(self, managed_collection_id):
        self._call("remove_managed_collection", managed_collection_id)

    async def add_custom_collection(self, custom_collection_name):
        self._call("add_custom_collection", custom_collection_name)
        return CookidooCollection(
            id=self._new_id("col"), name=custom_collection_name, description=None, chapters=[]
        )

    async def remove_custom_collection(self, custom_collection_id):
        self._call("remove_custom_collection", custom_collection_id)

    async def add_recipes_to_custom_collection(self, custom_collection_id, recipe_ids):
        self._call("add_recipes_to_custom_collection", custom_collection_id, recipe_ids)
        return replace(self.custom[0], id=custom_collection_id)

    async def remove_recipe_from_custom_collection(self, custom_collection_id, recipe_id):
        self._call("remove_recipe_from_custom_collection", custom_collection_id, recipe_id)
        return replace(self.custom[0], id=custom_collection_id)

    # Meal plan
    async def get_recipes_in_calendar_week(self, day: date, *, resolve_custom_recipes=False):
        self._call("get_recipes_in_calendar_week", day, resolve_custom_recipes)
        return self.days

    def _day(self, day: date) -> CookidooCalendarDay:
        return CookidooCalendarDay(id=day.isoformat(), title="", recipes=[])

    async def add_recipes_to_calendar(self, day, recipe_ids):
        self._call("add_recipes_to_calendar", day, recipe_ids)
        return self._day(day)

    async def add_custom_recipes_to_calendar(self, day, recipe_ids):
        self._call("add_custom_recipes_to_calendar", day, recipe_ids)
        return self._day(day)

    async def remove_recipe_from_calendar(self, day, recipe_id):
        self._call("remove_recipe_from_calendar", day, recipe_id)
        return self._day(day)

    async def remove_custom_recipe_from_calendar(self, day, recipe_id):
        self._call("remove_custom_recipe_from_calendar", day, recipe_id)
        return self._day(day)


SECRET = "fake-password-for-tests-0001"


def request_error(detail: str = "") -> CookidooRequestException:
    """A library error whose text contains secrets, like a raw failed HTTP exchange might."""
    return CookidooRequestException(
        f"Request failed {detail} headers={{'Authorization': 'Bearer abc.def.ghi'}} "
        f"password={SECRET}"
    )


@pytest.fixture
def fake(monkeypatch) -> FakeCookidoo:
    """A CookidooService on top of FakeCookidoo, installed as the server's service."""
    api = FakeCookidoo()
    svc = CookidooService(
        CookidooSettings(email="test@example.com", password=SECRET, country="gb", language="en-GB")
    )

    async def client():
        return api

    monkeypatch.setattr(svc, "client", client)
    monkeypatch.setattr(server, "_cookidoo_service", svc)
    return api


async def run(tool: str, **args: Any) -> str:
    """Call a tool through the MCP protocol (in-memory client) and return its text."""
    async with Client(server.mcp) as client:
        result = await client.call_tool(tool, args)
        return result.content[0].text  # type: ignore[union-attr]
