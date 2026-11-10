"""Release metadata stays consistent: version, registry entry, bundle manifest, Docker label."""

import importlib.util
import json
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
SERVER_JSON = json.loads((ROOT / "server.json").read_text())
MANIFEST = json.loads((ROOT / "mcpb" / "manifest.json").read_text())
README = (ROOT / "README.md").read_text()


def test_registry_entry_matches_the_release():
    assert SERVER_JSON["version"] == VERSION
    (package,) = SERVER_JSON["packages"]
    assert package["registryType"] == "oci"
    assert package["identifier"] == f"ghcr.io/herp75/mcp-cookidoo:{VERSION}"
    assert len(SERVER_JSON["description"]) <= 100  # registry limit


def test_docker_label_proves_registry_ownership():
    label = f'LABEL io.modelcontextprotocol.server.name="{SERVER_JSON["name"]}"'
    assert label in (ROOT / "Dockerfile").read_text()


def test_changelog_has_a_section_for_the_version():
    assert re.search(
        rf"^## \[{re.escape(VERSION)}\] - \d{{4}}-\d{{2}}-\d{{2}}$",
        (ROOT / "CHANGELOG.md").read_text(),
        re.MULTILINE,
    )


@pytest.mark.parametrize(
    "names",
    [
        [v["name"] for v in SERVER_JSON["packages"][0]["environmentVariables"]],
        list(MANIFEST["server"]["mcp_config"]["env"]),
    ],
)
def test_declared_variables_are_documented(names):
    for name in names:
        assert f"`{name}`" in README, f"{name} is not in the README configuration table"


def test_bundle_maps_every_user_setting():
    env = MANIFEST["server"]["mcp_config"]["env"]
    used = {m for value in env.values() for m in re.findall(r"\$\{user_config\.(\w+)\}", value)}
    assert used == set(MANIFEST["user_config"])
    assert MANIFEST["user_config"]["password"]["sensitive"] is True
    assert MANIFEST["server"]["type"] == "uv"


def test_bundle_manifest_lists_every_tool():
    spec = importlib.util.spec_from_file_location("build_mcpb", ROOT / "scripts" / "build_mcpb.py")
    assert spec and spec.loader
    build_mcpb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build_mcpb)

    manifest = build_mcpb.manifest()
    snapshot = json.loads((ROOT / "tests" / "snapshots" / "tools.json").read_text())["tools"]
    assert manifest["version"] == VERSION
    assert [t["name"] for t in manifest["tools"]] == sorted(snapshot)
    assert all(t["description"] for t in manifest["tools"])
