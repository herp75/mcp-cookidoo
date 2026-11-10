"""
Build the Claude Desktop bundle (.mcpb): stage the server files with a manifest whose version
and tool/prompt lists come from the code, then pack it with the mcpb CLI.

    uv run python scripts/build_mcpb.py            # -> dist/mcp-cookidoo-<version>.mcpb
    uv run python scripts/build_mcpb.py --stage-only

The bundle uses the "uv" server type: Claude Desktop installs Python and the locked
dependencies from pyproject.toml / uv.lock; nothing is vendored.
"""

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MCPB_CLI = "@anthropic-ai/mcpb@2.1.2"
FILES = [
    "pyproject.toml",
    "uv.lock",
    "LICENSE",
    "NOTICE",
    "README.md",
    "server.py",
    "cookidoo_service.py",
    "guided.py",
    "schemas.py",
    "security.py",
    "recipe_images.py",
    "devices.py",
    "prompt.md",
    "prompt_FR.md",
    "prompt_DE.md",
]


def version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


async def _tools() -> list[dict]:
    from fastmcp import Client

    import server

    async with Client(server.mcp) as client:
        tools = await client.list_tools()
    return sorted(
        (
            {"name": t.name, "description": (t.description or "").strip().split("\n")[0]}
            for t in tools
        ),
        key=lambda t: t["name"],
    )


def manifest() -> dict:
    data = json.loads((ROOT / "mcpb" / "manifest.json").read_text())
    data["version"] = version()
    data["tools"] = asyncio.run(_tools())
    data["tools_generated"] = False
    # create_thermomix_recipe is built by the server (dish, language): not a fixed text
    data["prompts_generated"] = True
    return data


def stage(target: Path) -> Path:
    if target.exists():
        shutil.rmtree(target)
    (target / "tools").mkdir(parents=True)
    for name in FILES:
        shutil.copy2(ROOT / name, target / name)
    for module in sorted((ROOT / "tools").glob("*.py")):
        shutil.copy2(module, target / "tools" / module.name)
    (target / "manifest.json").write_text(
        json.dumps(manifest(), indent=2, ensure_ascii=False) + "\n"
    )
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--stage-only", action="store_true")
    args = parser.parse_args()
    staged = stage(ROOT / "build" / "mcpb")
    print(f"staged {staged}")
    if args.stage_only:
        return
    output = ROOT / "dist" / f"mcp-cookidoo-{version()}.mcpb"
    output.parent.mkdir(exist_ok=True)
    subprocess.run(["npx", "-y", MCPB_CLI, "validate", str(staged / "manifest.json")], check=True)
    subprocess.run(["npx", "-y", MCPB_CLI, "pack", str(staged), str(output)], check=True)
    print(f"built {output}")


if __name__ == "__main__":
    main()
