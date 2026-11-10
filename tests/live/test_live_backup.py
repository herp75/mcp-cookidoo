"""
Live test of backup.py: an own recipe and an own collection with a catalogue recipe are
backed up; a second run downloads nothing it already has. Removes what it created.
"""

import pytest

from backup import Backup
from tests.live.conftest import LiveService, call, find_id, name

pytestmark = [pytest.mark.live, pytest.mark.live_write, pytest.mark.asyncio(loop_scope="session")]


async def test_backup_round_trip(live: LiveService, catalog_recipe: str, tmp_path):
    uploaded = await call(
        live,
        "upload_custom_recipe",
        recipe_json=(
            '{"name": "' + name("backup") + '", "ingredients": ["1 Ei", "Salz"], '
            '"steps": ["Ei kochen.", {"text": "Salz zugeben und rühren {settings}.", '
            '"settings": [{"time": 60, "speed": "2"}], '
            '"ingredients": [{"text": "Salz", "ingredient": "Salz"}]}], '
            '"servings": 2, "prep_time": 5, "total_time": 10, "hints": ["Tipp"]}'
        ),
    )
    recipe_id = find_id("Recipe ID", uploaded)
    created = await call(live, "create_collection", name=name("backup collection"))
    collection_id = find_id("Collection ID", created)
    try:
        await call(
            live, "add_to_collection", collection_id=collection_id, recipe_ids=[catalog_recipe]
        )
        first = await Backup(live, tmp_path, delay=0).run()
        assert not first.failed, first.failed
        own = next((tmp_path / "own").glob(f"*--{recipe_id}.md")).read_text()
        assert "1. Ei kochen." in own and "1 Min./Stufe 2" in own and "- Tipp" in own
        assert next((tmp_path / "own").glob(f"*--{recipe_id}.json"))
        catalogue = next((tmp_path / "catalogue").glob(f"*--{catalog_recipe}.md")).read_text()
        assert "## Ingredients" in catalogue and "## Steps" in catalogue
        collection = next((tmp_path / "collections").glob(f"*--{collection_id}.md")).read_text()
        assert f"--{catalog_recipe}.md)" in collection
        assert first.downloaded >= 1

        second = await Backup(live, tmp_path, delay=0).run()
        assert second.own_unchanged_on_cookidoo  # one empty 304 for all own recipes
        assert second.downloaded == 0 and second.written == 0
    finally:
        await call(live, "delete_collection", collection_id=collection_id, confirm=True)
        await call(live, "delete_custom_recipe", recipe_id=recipe_id, confirm=True)

    third = await Backup(live, tmp_path, delay=0).run()
    assert third.marked == 3  # recipe deleted, collection deleted, catalogue recipe in none
    assert "Deleted on Cookidoo" in next((tmp_path / "own").glob(f"*--{recipe_id}.md")).read_text()
