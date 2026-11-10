"""
The tool contract: names, annotations and confirm parameters.

tests/snapshots/tools.json is the reviewed list. Adding, removing or re-annotating a tool must
update it deliberately in the same PR:  UPDATE_SNAPSHOTS=1 uv run pytest tests/test_tool_contract.py
"""

import json
import os
from pathlib import Path

import pytest
from fastmcp import Client

import server

SNAPSHOT = Path(__file__).parent / "snapshots" / "tools.json"
DESTRUCTIVE_PREFIXES = ("delete_", "remove_", "clear_", "unfollow_")
# Tools that overwrite existing data without a destructive-sounding name
OVERWRITING = {"update_custom_recipe", "edit_shopping_list_items"}
# Irreversible tools that must also require confirm=true
NEEDS_CONFIRM_PREFIXES = ("delete_", "clear_")


async def current_contract() -> dict:
    async with Client(server.mcp) as client:
        tools = await client.list_tools()
        prompts = await client.list_prompts()
    contract = {}
    for t in sorted(tools, key=lambda t: t.name):
        a = t.annotations
        assert a is not None, f"{t.name} has no annotations"
        contract[t.name] = {
            # Keys use the MCP wire names; attributes the SDK v2 names
            "readOnlyHint": a.read_only_hint,
            "destructiveHint": a.destructive_hint,
            "idempotentHint": a.idempotent_hint,
            "openWorldHint": a.open_world_hint,
            "params": sorted(t.input_schema.get("properties", {})),
        }
    return {"tools": contract, "prompts": sorted(p.name for p in prompts)}


async def test_contract_matches_snapshot():
    contract = await current_contract()
    if os.getenv("UPDATE_SNAPSHOTS"):
        SNAPSHOT.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
    assert contract == json.loads(SNAPSHOT.read_text(encoding="utf-8")), (
        "Tool contract changed. Review it, then run UPDATE_SNAPSHOTS=1 uv run pytest "
        "tests/test_tool_contract.py and commit tests/snapshots/tools.json."
    )


@pytest.fixture(scope="module")
def tools() -> dict:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))["tools"]


def test_destructive_tools_are_annotated(tools):
    for name, t in tools.items():
        if name.startswith(DESTRUCTIVE_PREFIXES) or name in OVERWRITING:
            assert t["destructiveHint"] is True, name
            assert t["readOnlyHint"] is False, name


def test_read_tools_are_read_only(tools):
    for name, t in tools.items():
        if name.startswith(("get_", "list_", "search_")) or name == "generate_recipe_structure":
            assert t["readOnlyHint"] is True, name
        if t["readOnlyHint"]:
            assert not t["destructiveHint"], name


def test_irreversible_tools_require_confirm(tools):
    for name, t in tools.items():
        if name.startswith(NEEDS_CONFIRM_PREFIXES):
            assert "confirm" in t["params"], name


def test_every_tool_is_annotated(tools):
    for name, t in tools.items():
        assert t["readOnlyHint"] is not None and t["openWorldHint"] is True, name
