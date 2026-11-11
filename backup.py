"""
Back up a Cookidoo account to Markdown: the own (custom) recipes, the collections, and the
catalogue recipes in those collections. Only reads from Cookidoo; never changes anything there.

    uv run backup.py ~/cookidoo-backup

Each run only downloads what changed:
- own recipes: one request for all of them (Cookidoo lists them in full), answered with an
  empty "304 Not Modified" when nothing changed since the last run;
- collections: their lists, a few small requests;
- catalogue recipes: each new one once; a copy older than 90 days is downloaded again to pick
  up Vorwerk's changes, at most 1/12 of them per run so that it spreads over the runs.

Files are only rewritten when their content changes, so in a git repository each commit shows
what changed on Cookidoo. Nothing is ever deleted: a recipe that disappears from Cookidoo or
from all collections keeps its file, marked as such.

Layout:
    README.md                  index
    own/<name>--<id>.md        own recipes; <name>--<id>.json holds the recipe in the format
                               upload_custom_recipe accepts, and Cookidoo's raw JSON
    catalogue/<name>--<id>.md  recipes from the collections
    collections/<name>--<id>.md
    .state.json                what was downloaded when (git-ignored; losing it only costs
                               one fuller run)

Exit status: 0 done; 1 aborted (e.g. login or a list failed: nothing was marked as removed);
2 done, but some recipes could not be read (their previous copy is kept).
"""

import argparse
import asyncio
import html
import json
import math
import os
import re
import sys
import tempfile
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from cookidoo_api import CookidooCollection, CookidooLocalizationConfig
from cookidoo_api.helpers import _construct_recipe_url, cookidoo_custom_recipe_from_json

from cookidoo_service import CookidooService, load_cookidoo_settings
from guided import editable_recipe, label
from security import ID_PATTERN, install_log_redaction, redact

STATE_FILE = ".state.json"
OWN, CATALOGUE, COLLECTIONS = "own", "catalogue", "collections"
DELETED = "> **Deleted on Cookidoo** (noticed on {day}). This is the last version backed up."
UNLISTED = "> **No longer in any of your collections** (noticed on {day})."
GONE = "> **This collection is no longer on Cookidoo** (noticed on {day})."
MARKER = re.compile(r"^> \*\*(Deleted on Cookidoo|No longer in any|This collection is no)")
MAX_NAME = 60
REFRESH_SPREAD = 12  # at most 1/12 of the catalogue recipes are downloaded again per run


# --- Text --------------------------------------------------------------------------------


# Symbols of Cookidoo's icon font (Unicode private use area) in catalogue steps, seen in the
# editor snapshots: the mode symbols follow the mode's name ("Teig kneten \ue001/2 Min."), so
# they are dropped; the reverse symbol carries meaning of its own and becomes a word.
MODE_SYMBOLS = "\ue001\ue008\ue00b\ue00d\ue019\ue01e\ue026"
REVERSE_SYMBOL = "\ue003"
UNKNOWN_SYMBOL = "[?]"


def text(value: object, language: str = "en") -> str:
    """Cookidoo text as one plain line: markup removed, entities decoded, whitespace collapsed."""
    plain = html.unescape(re.sub(r"<[^>]+>", "", str(value or "")))
    plain = plain.translate({ord(c): None for c in MODE_SYMBOLS})
    plain = plain.replace(REVERSE_SYMBOL, label("reverse", language))
    plain = re.sub("[\ue000-\uf8ff]", UNKNOWN_SYMBOL, plain)
    plain = re.sub(r"\s+([/.,])", r"\1", re.sub(r"\s+", " ", plain))
    return plain.strip()


def link_text(value: object) -> str:
    return text(value).replace("[", "\\[").replace("]", "\\]")


def slug(name: str) -> str:
    """A file-name part from a recipe name: ASCII letters, digits and dashes."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    cleaned = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return cleaned[:MAX_NAME].rstrip("-") or "recipe"


def duration(seconds: object) -> str | None:
    """Seconds (or an ISO 8601 duration like PT1H30M) as "1 h 30 min"."""
    if isinstance(seconds, str) and (
        m := re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:\d+S)?", seconds)
    ):
        seconds = int(m[1] or 0) * 3600 + int(m[2] or 0) * 60
    if not isinstance(seconds, int | float) or isinstance(seconds, bool) or seconds <= 0:
        return None
    hours, minutes = divmod(round(seconds / 60), 60)
    return (
        " ".join(
            p for p in (f"{hours} h" if hours else "", f"{minutes} min" if minutes else "") if p
        )
        or "1 min"
    )


def _amount(quantity: object) -> str:
    if not isinstance(quantity, dict):
        return ""
    if quantity.get("value") is not None:
        return _number(quantity["value"])
    if quantity.get("from") is not None:
        to = quantity.get("to")
        return _number(quantity["from"]) + (f"–{_number(to)}" if to is not None else "")
    return ""


def _number(value: object) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return text(value)


def _items(value: object) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _day(timestamp: object) -> str:
    return text(timestamp)[:10]


def _section(title: str, lines: Iterable[str]) -> list[str]:
    lines = [line for line in lines if line.strip()]
    return ["", f"## {title}", "", *lines] if lines else []


# --- Rendering ---------------------------------------------------------------------------


def render_own(item: dict, localization: CookidooLocalizationConfig) -> tuple[str, str, str]:
    """An own recipe from Cookidoo's raw JSON: (name, Markdown, JSON file content)."""
    recipe = cookidoo_custom_recipe_from_json(item, localization)  # type: ignore[arg-type]
    content = item.get("recipeContent") or {}
    instructions = content.get("instructions") or []
    upload, lost_in = editable_recipe(recipe, list(instructions))
    name = text(recipe.name) or "Unnamed recipe"

    unit = text((content.get("yield") or content.get("recipeYield") or {}).get("unitText"))
    times = [
        f"{label}: {value}"
        for label, value in (
            ("Preparation", duration(recipe.active_time)),
            ("Total", duration(recipe.total_time)),
        )
        if value
    ]
    facts = [
        f"- Cookidoo: [{item.get('recipeId')}]({recipe.url})",
        f"- Servings: {recipe.serving_size} {unit}".rstrip(),
        f"- {' · '.join(times)}" if times else "",
        f"- Thermomix: {', '.join(text(t) for t in recipe.tools)}" if recipe.tools else "",
        f"- Created {_day(item.get('createdAt'))}, last changed {_day(item.get('modifiedAt'))}",
    ]
    steps = [text(step.get("text") if isinstance(step, dict) else step) for step in instructions]
    lines = [f"# {name}", "", *(f for f in facts if f)]
    if lost_in:
        lines += [
            "",
            f"> Steps {', '.join(map(str, lost_in))} have machine settings that a re-upload of "
            "the `upload_custom_recipe` part of the JSON file would lose; the `cookidoo` part "
            "has them.",
        ]
    lines += _section("Ingredients", (f"- {text(i)}" for i in recipe.ingredients))
    lines += _section("Steps", (f"{n}. {s}" for n, s in enumerate(filter(None, steps), 1)))
    lines += _section("Hints", (f"- {text(h)}" for h in recipe.hints))
    data = {
        "about": "upload_custom_recipe: this recipe as the MCP server's upload_custom_recipe "
        "accepts it. cookidoo: the recipe exactly as Cookidoo returned it.",
        "upload_custom_recipe": upload,
        "cookidoo": item,
    }
    return name, "\n".join(lines) + "\n", json.dumps(data, indent=2, ensure_ascii=False) + "\n"


TIME_LABELS = {"activeTime": "Active", "totalTime": "Total", "bakingTime": "Baking",
               "restingTime": "Resting", "cookingTime": "Cooking"}  # fmt: skip


NUTRIENTS = {"kcal": "", "kJ": "", "protein": "protein", "fat": "fat",
             "carb2": "carbohydrates", "dietaryFibre": "fibre"}  # fmt: skip


def render_catalogue(raw: dict, localization: CookidooLocalizationConfig) -> tuple[str, str]:
    """A catalogue recipe from Cookidoo's raw JSON: (name, Markdown)."""
    name = text(raw.get("title")) or "Unnamed recipe"
    recipe_id = text(raw.get("id"))
    serving = raw.get("servingSize") or {}
    times = [
        f"{TIME_LABELS.get(text(t.get('type')), text(t.get('type')))}: {value}"
        for t in _items(raw.get("times"))
        if (value := duration((t.get("quantity") or {}).get("value")))
    ]
    devices = [text(v) for v in raw.get("thermomixVersions") or [] if text(v)]
    facts = [
        f"- Cookidoo: [{recipe_id}]({_construct_recipe_url(localization, recipe_id)})",
        f"- Servings: {_amount(serving.get('quantity'))} {text(serving.get('unitNotation'))}",
        f"- {' · '.join(times)}" if times else "",
        f"- Difficulty: {text(raw.get('difficulty'))}" if raw.get("difficulty") else "",
        f"- Thermomix: {', '.join(devices)}" if devices else "",
        f"- Published {_day(raw.get('publicationDate'))}" if raw.get("publicationDate") else "",
    ]
    lines = [f"# {name}", "", *(f for f in facts if f.strip(" -:"))]

    ingredients: list[str] = []
    for group in _items(raw.get("recipeIngredientGroups")):
        if title := text(group.get("title")):
            ingredients += ["", f"### {title}", ""]
        for i in _items(group.get("recipeIngredients")):
            words = [_amount(i.get("quantity")), text(i.get("unitNotation")),
                     text(i.get("ingredientNotation"))]  # fmt: skip
            line = " ".join(w for w in words if w)
            if preparation := text(i.get("preparation")):
                line += f" {preparation}" if preparation.startswith("(") else f", {preparation}"
            ingredients.append(f"- {line}" + (" (optional)" if i.get("optional") else ""))
    lines += _section("Ingredients", ingredients)

    utensils = [_first_text(u) for u in raw.get("recipeUtensils") or []]
    lines += _section("Utensils", (f"- {u}" for u in utensils if u))

    steps: list[str] = []
    number = 0
    for group in _items(raw.get("recipeStepGroups")):
        if title := text(group.get("title")):
            steps += ["", f"### {title}", ""]
        for step in _items(group.get("recipeSteps")):
            if step_text := text(step.get("formattedText"), localization.language):
                number += 1
                steps.append(f"{number}. {step_text}")
    lines += _section("Steps", steps)

    nutrition: list[str] = []
    for group in _items(raw.get("nutritionGroups")):
        for entry in _items(group.get("recipeNutritions")):
            per = f"{_amount({'value': entry.get('quantity')})} {text(entry.get('unitNotation'))}"
            values = ", ".join(
                " ".join(
                    w
                    for w in (
                        NUTRIENTS.get(text(n.get("type")), text(n.get("type"))),
                        _number(n.get("number")),
                        text(n.get("unittype")),
                    )
                    if w
                )
                for n in _items(entry.get("nutritions"))
            )
            if values:
                nutrition.append(f"- Per {per.strip()}: {values}")
    lines += _section("Nutrition", nutrition)

    notes = [
        f"- {content}"
        for n in _items(raw.get("additionalInformation"))
        if (content := text(n.get("content")))
    ]
    lines += _section("Notes", notes)
    return name, "\n".join(lines) + "\n"


def _first_text(value: object) -> str:
    if isinstance(value, dict):
        for key in ("utensilNotation", "title", "name", "text"):
            if text(value.get(key)):
                return text(value[key])
        return ""
    return text(value)


def render_collection(
    collection: CookidooCollection, followed: bool, link: Callable[[str], str | None]
) -> str:
    kind = "Followed Cookidoo collection" if followed else "Own collection"
    lines = [
        f"# {text(collection.name) or 'Unnamed collection'}",
        "",
        f"{kind}, ID `{collection.id}`",
    ]
    if description := text(collection.description):
        lines += ["", description]
    for chapter in collection.chapters:
        entries = []
        for recipe in chapter.recipes:
            target = link(recipe.id)
            label = link_text(recipe.name) or recipe.id
            entries.append(f"- [{label}](../{target})" if target else f"- {label} (`{recipe.id}`)")
        if entries:
            lines += ["", f"## {text(chapter.name)}" if text(chapter.name) else "## Recipes", ""]
            lines += entries
    return "\n".join(lines) + "\n"


# --- Files -------------------------------------------------------------------------------


def write_if_changed(path: Path, content: str) -> bool:
    """Write atomically, only when the content differs. Returns whether it was written."""
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return True


def files_by_id(folder: Path) -> dict[str, Path]:
    """The Markdown files of a folder, by the ID at the end of their name."""
    found = {}
    for path in sorted(folder.glob("*--*.md")):
        found[path.stem.rsplit("--", 1)[1]] = path
    return found


def mark(path: Path, marker: str) -> bool:
    """Add a marker line under the title, once."""
    content = path.read_text(encoding="utf-8")
    title, _, rest = content.partition("\n")
    if MARKER.match(rest.lstrip("\n")):
        return False
    return write_if_changed(path, f"{title}\n\n{marker}\n{rest}")


def unmark(path: Path) -> bool:
    content = path.read_text(encoding="utf-8")
    title, _, rest = content.partition("\n")
    body = rest.lstrip("\n")
    if not MARKER.match(body):
        return False
    return write_if_changed(path, f"{title}\n\n{body.partition(chr(10))[2].lstrip(chr(10))}")


def is_marked(path: Path) -> bool:
    return bool(MARKER.match(path.read_text(encoding="utf-8").partition("\n")[2].lstrip("\n")))


def title_of(path: Path) -> str:
    return path.read_text(encoding="utf-8").partition("\n")[0].removeprefix("# ").strip()


# --- Backup ------------------------------------------------------------------------------


@dataclass
class Result:
    own: int = 0
    own_written: int = 0
    own_unchanged_on_cookidoo: bool = False
    collections: int = 0
    catalogue: int = 0
    downloaded: int = 0
    written: int = 0
    marked: int = 0
    failed: list[str] = field(default_factory=list)

    def summary(self) -> str:
        own = (
            "unchanged on Cookidoo (one empty response)"
            if self.own_unchanged_on_cookidoo
            else f"{self.own}, {self.own_written} file(s) written"
        )
        lines = [
            f"Own recipes: {own}",
            f"Collections: {self.collections}",
            f"Catalogue recipes: {self.catalogue}, {self.downloaded} downloaded, "
            f"{self.written} file(s) written",
            f"Marked as removed: {self.marked}",
        ]
        lines += [f"Failed: {failure}" for failure in self.failed]
        return "\n".join(lines)


class Backup:
    def __init__(
        self,
        service: CookidooService,
        root: Path,
        *,
        refresh_days: int = 90,
        delay: float = 0.3,
        today: date | None = None,
    ):
        self.service, self.root = service, root
        self.refresh_days, self.delay = max(1, refresh_days), delay
        self.day = today or date.today()
        self.today = self.day.isoformat()
        self.result = Result()
        self.state: dict[str, Any] = {}

    def _load_state(self) -> None:
        try:
            state = json.loads((self.root / STATE_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        self.state = state if isinstance(state, dict) else {}
        self.state.setdefault("fetched", {})

    def _save_state(self) -> None:
        write_if_changed(
            self.root / STATE_FILE, json.dumps(self.state, indent=2, sort_keys=True) + "\n"
        )

    async def run(self) -> Result:
        self.root.mkdir(parents=True, exist_ok=True)
        write_if_changed(self.root / ".gitignore", f"{STATE_FILE}\n.tmp-*\n")
        self._load_state()
        localization = (await self.service.client()).localization
        own_ids = await self._own(localization)
        followed = await self.service.all_collections(managed=True)
        own_collections = await self.service.all_collections(managed=False)
        await self._catalogue(localization, own_ids, [*followed, *own_collections])
        self._collections(followed, own_collections)
        self._index()
        self._save_state()
        return self.result

    async def _own(self, localization: CookidooLocalizationConfig) -> set[str]:
        folder = self.root / OWN
        existing = files_by_id(folder)
        known = set(self.state.get("own_ids") or [])
        # Use the ETag only while every recipe it covers still has its file
        etag = self.state.get("own_etag") if known and known <= set(existing) else None
        items, new_etag = await self.service.own_recipes_raw(etag)
        if items is None:
            self.result.own_unchanged_on_cookidoo = True
            self.result.own = len(known)
            return known
        seen = set()
        for item in items:
            recipe_id = str(item.get("recipeId") or "")
            if not ID_PATTERN.fullmatch(recipe_id):
                self.result.failed.append(f"own recipe with an unexpected ID: {recipe_id[:70]!r}")
                continue
            try:
                name, markdown, data = render_own(item, localization)
            except Exception as e:
                self.result.failed.append(redact(f"own recipe {recipe_id}: {e}"))
                continue
            seen.add(recipe_id)
            path = folder / f"{slug(name)}--{recipe_id}.md"
            self._rename(existing.get(recipe_id), path)
            changed = write_if_changed(path, markdown)
            changed |= write_if_changed(path.with_suffix(".json"), data)
            self.result.own_written += changed
        for recipe_id, path in existing.items():
            if recipe_id not in seen and path.exists():
                self.result.marked += mark(path, DELETED.format(day=self.today))
        self.result.own = len(seen)
        failed_own = any(f.startswith("own recipe") for f in self.result.failed)
        self.state["own_ids"] = sorted(seen)
        self.state["own_etag"] = None if failed_own else new_etag
        return seen

    @staticmethod
    def _rename(old: Path | None, new: Path) -> None:
        """The recipe was renamed on Cookidoo: move its files to the new name."""
        if old is None or old == new:
            return
        for suffix in (".md", ".json"):
            source = old.with_suffix(suffix)
            if source.exists():
                source.replace(new.with_suffix(suffix))

    async def _catalogue(
        self,
        localization: CookidooLocalizationConfig,
        own_ids: set[str],
        collections: list[CookidooCollection],
    ) -> None:
        folder = self.root / CATALOGUE
        existing = files_by_id(folder)
        wanted = {
            recipe.id
            for collection in collections
            for chapter in collection.chapters
            for recipe in chapter.recipes
            if recipe.id not in own_ids and ID_PATTERN.fullmatch(recipe.id)
        }
        fetched: dict[str, str] = self.state["fetched"]
        new = sorted(wanted - set(existing))
        stale_before = (self.day - timedelta(days=self.refresh_days)).isoformat()
        old = sorted(wanted & set(existing), key=lambda rid: (fetched.get(rid, ""), rid))
        stale = [rid for rid in old if fetched.get(rid, "") < stale_before]
        refresh = stale[: math.ceil(len(old) / REFRESH_SPREAD)]
        for index, recipe_id in enumerate([*new, *refresh]):
            if index:
                await asyncio.sleep(self.delay)
            try:
                raw = await self.service.catalogue_recipe_raw(recipe_id)
                name, markdown = render_catalogue(raw, localization)
            except Exception as e:
                self.result.failed.append(redact(f"catalogue recipe {recipe_id}: {e}"))
                continue
            self.result.downloaded += 1
            fetched[recipe_id] = self.today
            path = folder / f"{slug(name)}--{recipe_id}.md"
            self._rename(existing.get(recipe_id), path)
            existing[recipe_id] = path
            self.result.written += write_if_changed(path, markdown)
        for recipe_id, path in existing.items():
            if recipe_id in wanted:
                unmark(path)
            else:
                self.result.marked += mark(path, UNLISTED.format(day=self.today))
        self.result.catalogue = len(wanted)

    def _collections(
        self, followed: list[CookidooCollection], own: list[CookidooCollection]
    ) -> None:
        folder = self.root / COLLECTIONS
        existing = files_by_id(folder)
        own_files, catalogue_files = (
            files_by_id(self.root / OWN),
            files_by_id(self.root / CATALOGUE),
        )

        def link(recipe_id: str) -> str | None:
            for kind, files in ((OWN, own_files), (CATALOGUE, catalogue_files)):
                if recipe_id in files:
                    return f"{kind}/{files[recipe_id].name}"
            return None

        seen = set()
        for collections, is_followed in ((followed, True), (own, False)):
            for collection in collections:
                if not ID_PATTERN.fullmatch(collection.id):
                    continue
                seen.add(collection.id)
                path = folder / f"{slug(text(collection.name))}--{collection.id}.md"
                old = existing.get(collection.id)
                if old and old != path and old.exists():
                    old.unlink()
                write_if_changed(path, render_collection(collection, is_followed, link))
        for collection_id, path in existing.items():
            if collection_id not in seen and path.exists():
                self.result.marked += mark(path, GONE.format(day=self.today))
        self.result.collections = len(seen)

    def _index(self) -> None:
        lines = [
            "# Cookidoo backup",
            "",
            "Made by `backup.py` of mcp-cookidoo. Recipes are kept when they disappear from "
            "Cookidoo, marked as such. Own recipes can be uploaded again from their `.json` "
            "file (`upload_custom_recipe` part).",
            "",
            "Catalogue recipes are Vorwerk's content: keep this backup private.",
        ]
        for title, folder in (
            ("Collections", COLLECTIONS),
            ("Own recipes", OWN),
            ("Catalogue recipes", CATALOGUE),
        ):
            files = files_by_id(self.root / folder)
            current = sorted((p for p in files.values() if not is_marked(p)), key=_sort_key)
            removed = sorted((p for p in files.values() if is_marked(p)), key=_sort_key)
            lines += ["", f"## {title} ({len(current)})", ""]
            lines += [f"- [{link_text(title_of(p))}]({folder}/{p.name})" for p in current] or [
                "None."
            ]
            if removed:
                lines += ["", f"### No longer on Cookidoo ({len(removed)})", ""]
                lines += [f"- [{link_text(title_of(p))}]({folder}/{p.name})" for p in removed]
        write_if_changed(self.root / "README.md", "\n".join(lines) + "\n")


def _sort_key(path: Path) -> tuple[str, str]:
    return title_of(path).casefold(), path.name


# --- Command line ------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Back up a Cookidoo account to Markdown (read-only)."
    )
    parser.add_argument("folder", type=Path, help="backup folder (created if missing)")
    parser.add_argument(
        "--refresh-days",
        type=int,
        default=90,
        metavar="DAYS",
        help="download catalogue recipes again when their copy is older (default 90)",
    )
    parser.add_argument(
        "--delay", type=float, default=0.3, help="seconds between recipe downloads (default 0.3)"
    )
    args = parser.parse_args(argv)
    install_log_redaction()

    async def run() -> Result:
        service = CookidooService(load_cookidoo_settings())
        try:
            backup = Backup(service, args.folder, refresh_days=args.refresh_days, delay=args.delay)
            return await backup.run()
        finally:
            await service.close()

    try:
        result = asyncio.run(run())
    except Exception as e:
        print(redact(f"Backup aborted: {e}"), file=sys.stderr)
        return 1
    print(result.summary())
    return 2 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())
