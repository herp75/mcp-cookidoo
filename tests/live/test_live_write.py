"""
Live tests that change the test account. Each removes what it created, also when it fails;
leftovers of an interrupted run (named with PREFIX) are removed at the start of the next one.
The shopping list is never cleared: only items and recipes these tests added are removed.
"""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError
from PIL import Image

from tests.live.conftest import LiveService, call, find_id, name

pytestmark = [pytest.mark.live, pytest.mark.live_write, pytest.mark.asyncio(loop_scope="session")]

INGREDIENTS = ["200 g Reis", "1 Zwiebel", "20 g Butter"]
TM6_STEPS = [
    {
        "text": "Die Zwiebel zerkleinern {settings}.",
        "settings": [{"time": 5, "speed": "5"}],
        "ingredients": [{"text": "Die Zwiebel", "ingredient": "1 Zwiebel"}],
    },
    {
        "text": "Butter zugeben und dünsten {settings}.",
        "settings": [{"time": 180, "temperature": "120", "speed": "1", "reverse": True}],
    },
    {"text": "Teig kneten {settings}.", "settings": [{"mode": "dough", "time": 120}]},
    {"text": "Mixen {settings}.", "settings": [{"mode": "blend", "time": 30, "speed": "8"}]},
    {"text": "Kurz zerkleinern {settings}.", "settings": [{"mode": "turbo", "time": 1}]},
    {
        "text": "Warm halten {settings}.",
        "settings": [{"mode": "warm_up", "temperature": "65", "speed": "soft"}],
    },
    {"text": "Den Reis garen {settings}.", "settings": [{"mode": "rice_cooker"}]},
    {
        "text": "Im Varoma garen {settings}.",
        "settings": [{"time": 600, "temperature": "varoma", "speed": "1"}],
    },
    "Servieren.",
]
TM7_STEPS = [
    {
        "text": "Anbraten {settings}.",
        "settings": [{"mode": "browning", "time": 300, "temperature": "160", "power": "gentle"}],
    },
    {
        "text": "Dampfgaren {settings}.",
        "settings": [{"mode": "steaming", "time": 1200, "speed": "1", "accessory": "varoma"}],
    },
    {"text": "Zerkleinern {settings}.", "settings": [{"mode": "turbo", "time": 0.5, "pulses": 3}]},
]


async def _generate(live: LiveService, title: str, steps: list, device: str) -> str:
    text = await call(
        live,
        "generate_recipe_structure",
        name=name(title),
        ingredients=INGREDIENTS,
        steps=steps,
        servings=2,
        prep_time=10,
        total_time=40,
        hints=["Automatischer Test, wird wieder gelöscht."],
        device=device,
    )
    return text[: text.rindex("}") + 1]


async def _delete(live: LiveService, recipe_id: str) -> None:
    text = await call(live, "delete_custom_recipe", recipe_id=recipe_id, confirm=True)
    assert recipe_id in text


def _settings(recipe: dict) -> list[list[dict]]:
    return [step["settings"] for step in recipe["steps"] if isinstance(step, dict)]


async def test_custom_recipe_round_trip_tm6(live: LiveService):
    recipe_json = await _generate(live, "TM6", TM6_STEPS, "TM6")
    created = await call(live, "upload_custom_recipe", recipe_json=recipe_json, device="TM6")
    assert "created for the TM6" in created
    recipe_id = find_id("Recipe ID", created)
    try:
        listed = await call(live, "list_custom_recipes")
        assert recipe_id in listed

        text = await call(live, "get_custom_recipe", recipe_id=recipe_id)
        read = json.loads(text[text.index("{") :])
        sent = json.loads(recipe_json)
        assert read["name"] == sent["name"]
        assert read["ingredients"] == sent["ingredients"]
        assert read["servings"] == 2
        assert _settings(read) == _settings(sent), "settings changed on the way through Cookidoo"
        assert read["steps"][-1] == "Servieren."

        catalog = await live.get_mode_catalog(recipe_id)
        assert catalog.source == "cookidoo", catalog.note

        read["name"] = name("TM6 updated")
        read["steps"] = read["steps"][:3]
        updated = await call(
            live, "update_custom_recipe", recipe_id=recipe_id, recipe_json=json.dumps(read)
        )
        assert "updated" in updated
        assert "the recipe's devices" in updated, "device not taken from the recipe"
        again = await call(live, "get_custom_recipe", recipe_id=recipe_id)
        assert name("TM6 updated") in again

        with pytest.raises(ToolError, match="Not valid for the TM6"):
            await call(
                live,
                "update_custom_recipe",
                recipe_id=recipe_id,
                recipe_json=json.dumps({**read, "steps": TM7_STEPS}),
            )
    finally:
        await _delete(live, recipe_id)
    assert recipe_id not in await call(live, "list_custom_recipes")


async def test_custom_recipe_tm7_modes(live: LiveService):
    recipe_json = await _generate(live, "TM7", TM7_STEPS, "TM7")
    created = await call(live, "upload_custom_recipe", recipe_json=recipe_json, device="TM7")
    recipe_id = find_id("Recipe ID", created)
    try:
        text = await call(live, "get_custom_recipe", recipe_id=recipe_id)
        read = json.loads(text[text.index("{") :])
        assert _settings(read) == _settings(json.loads(recipe_json))
        device, source = await live.resolve_device(recipe_id=recipe_id)
        assert (device, source) == ("TM7", "the recipe's devices")
    finally:
        await _delete(live, recipe_id)


async def test_delete_needs_confirm(live: LiveService):
    recipe_json = await _generate(live, "confirm", ["Nur ein Schritt."], "TM6")
    recipe_id = find_id(
        "Recipe ID", await call(live, "upload_custom_recipe", recipe_json=recipe_json)
    )
    try:
        with pytest.raises(ToolError, match="confirm=true"):
            await call(live, "delete_custom_recipe", recipe_id=recipe_id)
        assert recipe_id in await call(live, "list_custom_recipes")
    finally:
        await _delete(live, recipe_id)


async def test_copy_and_image(live: LiveService, catalog_recipe: str, tmp_path: Path, monkeypatch):
    copied = await call(live, "copy_recipe_to_custom", recipe_id=catalog_recipe, servings=2)
    recipe_id = find_id("Custom recipe ID", copied)
    try:
        assert "(2 servings)" in copied
        photo = tmp_path / "photo.png"
        Image.new("RGB", (400, 300), (200, 120, 40)).save(photo)
        monkeypatch.setenv("COOKIDOO_IMAGE_DIRS", str(tmp_path))
        result = await call(
            live, "upload_custom_recipe_image", recipe_id=recipe_id, image_path=str(photo)
        )
        assert result.startswith(f"Image set for custom recipe {recipe_id}")
        with pytest.raises(ToolError, match="Image not uploaded"):
            await call(
                live, "upload_custom_recipe_image", recipe_id=recipe_id, image_path="/etc/passwd"
            )
    finally:
        await _delete(live, recipe_id)


async def test_collection(live: LiveService, catalog_recipe: str):
    created = await call(live, "create_collection", name=name("collection"))
    collection_id = find_id("Collection ID", created)
    try:
        await call(
            live, "add_to_collection", collection_id=collection_id, recipe_ids=[catalog_recipe]
        )
        listed = await call(live, "list_collections", kind="custom")
        assert collection_id in listed and catalog_recipe in listed
        await call(
            live, "remove_from_collection", collection_id=collection_id, recipe_ids=[catalog_recipe]
        )
        with pytest.raises(ToolError, match="confirm=true"):
            await call(live, "delete_collection", collection_id=collection_id)
    finally:
        await call(live, "delete_collection", collection_id=collection_id, confirm=True)
    assert collection_id not in await call(live, "list_collections", kind="custom")


async def test_shopping_list_items(live: LiveService):
    added = await call(live, "add_shopping_list_items", items=[name("Salz")])
    item_id = find_id("ID", added)
    try:
        renamed = await call(
            live, "edit_shopping_list_items", items=[{"id": item_id, "name": name("Pfeffer")}]
        )
        assert name("Pfeffer") in renamed
        await call(live, "mark_shopping_list_items", item_ids=[item_id], owned=True)
        assert f"[x] {name('Pfeffer')} | ID: {item_id}" in await call(live, "get_shopping_list")
        await call(live, "mark_shopping_list_items", item_ids=[item_id], owned=False)
    finally:
        await call(live, "remove_shopping_list_items", item_ids=[item_id])
    assert item_id not in await call(live, "get_shopping_list")


async def test_shopping_list_recipe(live: LiveService, catalog_recipe: str):
    recipes, _, _ = await live.get_shopping_list()
    if any(r.id == catalog_recipe for r in recipes):
        pytest.skip("the recipe is already on the test account's shopping list")
    await call(live, "add_recipes_to_shopping_list", recipe_ids=[catalog_recipe])
    try:
        assert catalog_recipe in await call(live, "get_shopping_list")
    finally:
        await call(live, "remove_recipes_from_shopping_list", recipe_ids=[catalog_recipe])
    assert f"ID: {catalog_recipe}" not in await call(live, "get_shopping_list")


async def test_meal_plan(live: LiveService, catalog_recipe: str):
    day = date.today() + timedelta(days=21)
    other = day + timedelta(days=1)
    await call(live, "add_to_meal_plan", day=day.isoformat(), recipe_ids=[catalog_recipe])
    planned = [day, other]
    try:
        week = await call(live, "get_meal_plan_week", day=day.isoformat())
        assert f"recipe ID: {catalog_recipe}" in week
        moved = await call(
            live,
            "move_in_meal_plan",
            recipe_id=catalog_recipe,
            from_day=day.isoformat(),
            to_day=other.isoformat(),
        )
        assert other.isoformat() in moved
        days = {d.id: d for d in await live.get_meal_plan_week(day)}
        if old_day := days.get(day.isoformat()):
            assert catalog_recipe not in [r.id for r in old_day.recipes]
        assert catalog_recipe in [r.id for r in days[other.isoformat()].recipes]
    finally:
        for d in planned:
            try:
                await live.remove_from_meal_plan(d, [catalog_recipe], [])
            except Exception:
                pass
    days = {d.id: d for d in await live.get_meal_plan_week(day)}
    for d in planned:
        assert catalog_recipe not in [r.id for r in getattr(days.get(d.isoformat()), "recipes", [])]
