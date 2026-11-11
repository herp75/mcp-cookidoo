"""backup.py against a fake service. Recipes are synthetic, shaped like Cookidoo's raw JSON."""

import copy
import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from cookidoo_api import (
    CookidooChapter,
    CookidooChapterRecipe,
    CookidooCollection,
    CookidooLocalizationConfig,
    CookidooRequestException,
)

import backup
from backup import Backup, slug, text
from schemas import CustomRecipe

DAY = date(2026, 11, 10)
OWN_ID = "01TESTRECIPE00000000000001"
ETAG = 'W/"own-1"'


def own_item(name="Testsuppe", recipe_id=OWN_ID, modified="2026-11-10T10:00:00.000Z"):
    return {
        "recipeId": recipe_id,
        "authorId": "author",
        "modifiedAt": modified,
        "createdAt": "2026-11-01T10:00:00.000Z",
        "status": "ACTIVE",
        "workStatus": "PRIVATE",
        "recipeContent": {
            "name": name,
            "image": "",
            "ingredients": [{"type": "INGREDIENT", "text": "1 Zwiebel"}],
            "instructions": [
                {"type": "STEP", "text": "Zwiebel schälen.", "annotations": []},
                {
                    "type": "STEP",
                    "text": "Zwiebel 5 Sek./Stufe 5 zerkleinern.",
                    "annotations": [
                        {
                            "type": "TTS",
                            "data": {"time": 5, "speed": "5"},
                            "position": {"offset": 8, "length": 13},
                        }
                    ],
                },
            ],
            "prepTime": 600,
            "totalTime": 1800,
            "tools": ["TM6"],
            "yield": {"value": 4, "unitText": "portion"},
            "hints": "Heiss servieren.",
        },
    }


def catalogue_raw(recipe_id, title="Testrisotto", step="Reis 3 Min./120°C//Stufe 1 dünsten."):
    return {
        "id": recipe_id,
        "title": title,
        "servingSize": {"quantity": {"value": 4}, "unitNotation": "Portionen"},
        "times": [
            {"type": "activeTime", "quantity": {"value": 900}},
            {"type": "totalTime", "quantity": {"value": 5400}},
        ],
        "difficulty": "easy",
        "thermomixVersions": ["TM6", "TM5"],
        "publicationDate": "2020-01-01T00:00:00Z",
        "exportDate": "2026-01-01T00:00:00Z",  # changes on Cookidoo's side; not written
        "recipeIngredientGroups": [
            {
                "title": "",
                "recipeIngredients": [
                    {
                        "quantity": {"value": 40},
                        "unitNotation": "g",
                        "ingredientNotation": "Parmesan",
                        "preparation": "in Stücken",
                    },
                    {
                        "quantity": {"from": 0.5, "to": 1},
                        "unitNotation": "TL",
                        "ingredientNotation": "Salz",
                        "preparation": "(nach Geschmack)",
                        "optional": True,
                    },
                ],
            }
        ],  # fmt: skip
        "recipeUtensils": [{"utensilNotation": "Spatel"}],
        "recipeStepGroups": [
            {
                "title": "",
                "recipeSteps": [
                    {"title": "1", "formattedText": "Teig kneten /2 Min. <nobr>fertig</nobr>."},
                    {"title": "2", "formattedText": step},
                ],
            }
        ],
        "nutritionGroups": [
            {
                "recipeNutritions": [
                    {
                        "quantity": 1,
                        "unitNotation": "Portion",
                        "nutritions": [
                            {"type": "kcal", "number": 433, "unittype": "kcal"},
                            {"type": "carb2", "number": 63, "unittype": "g"},
                        ],
                    }
                ]
            }
        ],
        "additionalInformation": [
            {"type": "VrkRecipeAdditionalInformationType", "content": "Salt &amp; pepper."}
        ],
    }


def collection(collection_id, name, recipe_ids):
    recipes = [
        CookidooChapterRecipe(id=rid, name=f"Recipe {rid}", total_time=60) for rid in recipe_ids
    ]
    return CookidooCollection(
        id=collection_id,
        name=name,
        description=None,
        chapters=[CookidooChapter(name="Hauptgerichte", recipes=recipes)],
    )


class FakeService:
    def __init__(self):
        self.own = [own_item()]
        self.etag = ETAG
        self.catalogue = {rid: catalogue_raw(rid) for rid in ("r100001", "r100002")}
        self.followed = [collection("col1", "Best of", ["r100001"])]
        self.custom = [collection("01TESTCOLLECTION0000000001", "Meine Liste", ["r100002", OWN_ID])]
        self.failures: dict[str, Exception] = {}
        self.etags_sent: list[str | None] = []
        self.downloads: list[str] = []

    async def client(self):
        return SimpleNamespace(
            localization=CookidooLocalizationConfig(
                country_code="ch", language="de-CH", url="https://cookidoo.ch/foundation/de-CH"
            )
        )

    async def own_recipes_raw(self, etag=None):
        self.etags_sent.append(etag)
        if "own" in self.failures:
            raise self.failures["own"]
        if etag == self.etag:
            return None, etag
        return copy.deepcopy(self.own), self.etag

    async def catalogue_recipe_raw(self, recipe_id):
        self.downloads.append(recipe_id)
        if recipe_id in self.failures:
            raise self.failures[recipe_id]
        return copy.deepcopy(self.catalogue[recipe_id])

    async def all_collections(self, managed):
        return list(self.followed if managed else self.custom)


@pytest.fixture
def fake():
    return FakeService()


async def run(fake, root, today=DAY, **kwargs):
    return await Backup(fake, root, today=today, delay=0, **kwargs).run()  # type: ignore[arg-type]


def read(root, folder, recipe_id, suffix=".md"):
    (path,) = (root / folder).glob(f"*--{recipe_id}{suffix}")
    return path.read_text(encoding="utf-8")


def snapshot(root):
    return {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.name != ".state.json"
    }


async def test_first_run_writes_everything(fake, tmp_path):
    result = await run(fake, tmp_path)
    assert (result.own, result.catalogue, result.collections, result.downloaded) == (1, 2, 2, 2)
    assert not result.failed
    own = read(tmp_path, "own", OWN_ID)
    assert own.startswith("# Testsuppe\n")
    assert "- Servings: 4 portion" in own and "- Preparation: 10 min · Total: 30 min" in own
    assert "2. Zwiebel 5 Sek./Stufe 5 zerkleinern." in own and "- Heiss servieren." in own
    assert "Created 2026-11-01, last changed 2026-11-10" in own
    data = json.loads(read(tmp_path, "own", OWN_ID, ".json"))
    assert data["cookidoo"] == own_item()
    CustomRecipe.model_validate(data["upload_custom_recipe"])  # can be uploaded again
    assert data["upload_custom_recipe"]["steps"][1]["settings"] == [
        {"mode": "manual", "time": 5, "temperature": None, "speed": "5", "reverse": False}
    ]

    risotto = read(tmp_path, "catalogue", "r100001")
    assert "- Cookidoo: [r100001](https://cookidoo.ch/recipes/recipe/de-CH/r100001)" in risotto
    assert "- Active: 15 min · Total: 1 h 30 min" in risotto
    assert "- 40 g Parmesan, in Stücken" in risotto
    assert "- 0.5–1 TL Salz (nach Geschmack) (optional)" in risotto
    assert "1. Teig kneten/2 Min. fertig." in risotto
    assert "2. Reis 3 Min./120°C/Linkslauf/Stufe 1 dünsten." in risotto
    assert "- Per 1 Portion: 433 kcal, carbohydrates 63 g" in risotto
    assert "- Salt & pepper." in risotto and "Vrk" not in risotto and "2026-01-01" not in risotto

    mine = read(tmp_path, "collections", "01TESTCOLLECTION0000000001")
    assert "Own collection" in mine and "## Hauptgerichte" in mine
    assert "(../catalogue/testrisotto--r100002.md)" in mine
    assert "(../own/testsuppe--01TESTRECIPE00000000000001.md)" in mine
    index = (tmp_path / "README.md").read_text()
    assert "## Own recipes (1)" in index and "## Catalogue recipes (2)" in index
    assert (tmp_path / ".gitignore").read_text() == ".state.json\n.tmp-*\n"


async def test_second_run_downloads_and_writes_nothing(fake, tmp_path):
    await run(fake, tmp_path)
    before = snapshot(tmp_path)
    fake.downloads.clear()
    result = await run(fake, tmp_path, today=DAY + timedelta(days=7))
    assert fake.etags_sent == [None, ETAG]
    assert result.own_unchanged_on_cookidoo and fake.downloads == []
    assert snapshot(tmp_path) == before
    assert "unchanged on Cookidoo" in result.summary()


async def test_changed_own_recipe_is_downloaded_with_the_list(fake, tmp_path):
    await run(fake, tmp_path)
    fake.own, fake.etag = [own_item(modified="2026-11-12T10:00:00.000Z")], 'W/"own-2"'
    result = await run(fake, tmp_path)
    assert result.own_written == 1
    assert "last changed 2026-11-12" in read(tmp_path, "own", OWN_ID)


async def test_etag_is_not_used_when_a_file_is_missing(fake, tmp_path):
    await run(fake, tmp_path)
    next((tmp_path / "own").glob("*.md")).unlink()
    await run(fake, tmp_path)
    assert fake.etags_sent == [None, None]
    assert read(tmp_path, "own", OWN_ID)


async def test_renamed_recipe_moves_its_files(fake, tmp_path):
    await run(fake, tmp_path)
    fake.own, fake.etag = [own_item(name="Zwiebelsuppe")], 'W/"own-2"'
    await run(fake, tmp_path)
    assert sorted(p.name for p in (tmp_path / "own").iterdir()) == [
        f"zwiebelsuppe--{OWN_ID}.json",
        f"zwiebelsuppe--{OWN_ID}.md",
    ]


async def test_deleted_recipe_is_kept_and_marked(fake, tmp_path):
    await run(fake, tmp_path)
    fake.own, fake.etag = [], 'W/"own-2"'
    result = await run(fake, tmp_path, today=DAY + timedelta(days=1))
    own = read(tmp_path, "own", OWN_ID)
    assert result.marked == 1 and "Deleted on Cookidoo** (noticed on 2026-11-11)" in own
    assert "1. Zwiebel schälen." in own  # the content is still there
    index = (tmp_path / "README.md").read_text()
    assert "## Own recipes (0)" in index and "### No longer on Cookidoo (1)" in index
    assert (await run(fake, tmp_path)).marked == 0  # marked once only

    fake.own, fake.etag = [own_item()], 'W/"own-3"'
    await run(fake, tmp_path)
    assert "Deleted" not in read(tmp_path, "own", OWN_ID)


async def test_catalogue_recipe_leaving_all_collections_is_marked_and_unmarked(fake, tmp_path):
    await run(fake, tmp_path)
    fake.followed = []
    result = await run(fake, tmp_path)
    assert result.marked == 2  # the recipe, and the collection that is gone
    assert "No longer in any of your collections" in read(tmp_path, "catalogue", "r100001")
    assert "no longer on Cookidoo" in read(tmp_path, "collections", "col1")

    fake.followed, fake.downloads = [collection("col1", "Best of", ["r100001"])], []
    await run(fake, tmp_path)
    assert "No longer" not in read(tmp_path, "catalogue", "r100001")
    assert fake.downloads == []  # back in a collection: no new download needed


async def test_old_copies_are_refreshed_a_share_per_run(fake, tmp_path):
    ids = [f"r2000{n:02d}" for n in range(30)]
    fake.catalogue = {rid: catalogue_raw(rid, title=f"Rezept {rid}") for rid in ids}
    fake.followed, fake.custom = [collection("col1", "Viele", ids)], []
    await run(fake, tmp_path)

    fake.downloads = []
    await run(fake, tmp_path, today=DAY + timedelta(days=89))
    assert fake.downloads == []  # not old enough yet

    fake.catalogue[ids[0]] = catalogue_raw(ids[0], title=f"Rezept {ids[0]}", step="Neu.")
    result = await run(fake, tmp_path, today=DAY + timedelta(days=91))
    assert fake.downloads == ids[:3]  # ceil(30 / 12), oldest first
    assert result.written == 1  # only the recipe that changed
    assert "2. Neu." in read(tmp_path, "catalogue", ids[0])

    fake.downloads = []
    await run(fake, tmp_path, today=DAY + timedelta(days=92))
    assert fake.downloads == ids[3:6]


async def test_a_failed_download_keeps_the_previous_copy(fake, tmp_path):
    await run(fake, tmp_path)
    before = read(tmp_path, "catalogue", "r100001")
    fake.catalogue["r100003"] = catalogue_raw("r100003")
    fake.followed = [collection("col1", "Best of", ["r100001", "r100003"])]
    fake.failures["r100003"] = CookidooRequestException("Cookidoo returned 403 password=hunter2")
    result = await run(fake, tmp_path, today=DAY + timedelta(days=100))
    assert result.failed == ["catalogue recipe r100003: Cookidoo returned 403 password=***"]
    assert read(tmp_path, "catalogue", "r100001") == before
    assert "r100003" in fake.downloads and not list((tmp_path / "catalogue").glob("*r100003*"))


async def test_unexpected_ids_never_become_paths(fake, tmp_path):
    fake.own = [own_item(recipe_id="../../evil"), own_item()]
    fake.followed = [collection("../col", "X", ["../../etc/passwd", "r100001"])]
    result = await run(fake, tmp_path)
    assert any("unexpected ID" in f for f in result.failed)
    assert "../../etc/passwd" not in fake.downloads
    names = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*")}
    assert all(".." not in n and "evil" not in n for n in names)


async def test_a_failing_list_aborts_before_marking_anything(fake, tmp_path):
    await run(fake, tmp_path)
    fake.failures["own"] = CookidooRequestException("Cookidoo returned 500")
    with pytest.raises(CookidooRequestException):
        await run(fake, tmp_path)
    assert "Deleted" not in read(tmp_path, "own", OWN_ID)


def _main_with(monkeypatch, failures):
    class Service(FakeService):
        def __init__(self, settings):
            super().__init__()
            self.failures.update(failures)

        async def close(self):
            pass

    monkeypatch.setattr(backup, "CookidooService", Service)
    monkeypatch.setattr(backup, "load_cookidoo_settings", lambda: None)


def test_main_exit_status_when_aborted(tmp_path, monkeypatch, capsys):
    _main_with(monkeypatch, {"own": CookidooRequestException("Cookidoo returned 500")})
    assert backup.main([str(tmp_path)]) == 1
    assert "Backup aborted: Cookidoo returned 500" in capsys.readouterr().err


def test_main_reports_partial_failures(tmp_path, monkeypatch, capsys):
    _main_with(monkeypatch, {"r100002": CookidooRequestException("Cookidoo returned 403")})
    assert backup.main([str(tmp_path), "--delay", "0"]) == 2
    out = capsys.readouterr().out
    assert "Own recipes: 1, 1 file(s) written" in out and "Failed: catalogue recipe r100002" in out


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Crème brûlée à l'orange", "creme-brulee-a-l-orange"),
        ("   ", "recipe"),
        ("A" * 80, "a" * 60),
    ],
)
def test_slug(name, expected):
    assert slug(name) == expected


def test_text_handles_symbols_and_markup():
    assert text("1 Min.//Stufe 1", "fr-FR") == "1 Min./sens inverse/Stufe 1"
    assert text("Turbo /1 Sek.") == "Turbo/1 Sek."
    assert text("Neu  Symbol") == "Neu [?] Symbol"
    assert text("<b>a</b>\n  b &lt;3") == "a b <3"


# --- CookidooService: raw JSON with ETags ---------------------------------------------------


class Response:
    def __init__(self, status, body=b"", headers=None):
        self.status, self.body, self.headers = status, body, headers or {}
        self.content = self

    async def iter_chunked(self, size):
        for i in range(0, len(self.body), size):
            yield self.body[i : i + size]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class Session:
    def __init__(self, response):
        self.response, self.requests = response, []

    def get(self, url, **kwargs):
        self.requests.append((str(url), kwargs))
        return self.response

    async def close(self):
        pass


@pytest.fixture
def service(monkeypatch):
    from unittest.mock import AsyncMock

    from yarl import URL

    from cookidoo_service import CookidooService, CookidooSettings

    svc = CookidooService(CookidooSettings(email="a@b.c", password="x"))
    api = SimpleNamespace(
        _api_headers={"AUTHORIZATION": "Bearer t"},
        _ensure_endpoints=AsyncMock(),
        api_endpoint=URL("https://cookidoo.example"),
        _path=lambda name: {"customer-recipes:recipe-create": "created-recipes/{language}",
                            "recipe:details": "recipes/recipe/{language}/{id}"}[name],
        localization=CookidooLocalizationConfig(country_code="ch", language="de-CH"),
    )  # fmt: skip
    monkeypatch.setattr(svc, "client", AsyncMock(return_value=api))

    def install(response):
        svc._session = Session(response)  # type: ignore[assignment]
        return svc._session

    return svc, install


async def test_own_recipes_raw_sends_the_etag_and_reads_a_304(service):
    svc, install = service
    session = install(Response(304))
    assert await svc.own_recipes_raw('W/"1"') == (None, 'W/"1"')
    ((url, kwargs),) = session.requests
    assert url == "https://cookidoo.example/created-recipes/de-CH"
    assert kwargs["headers"]["If-None-Match"] == 'W/"1"' and kwargs["allow_redirects"] is False


async def test_own_recipes_raw_returns_the_items_and_new_etag(service):
    svc, install = service
    body = json.dumps({"meta": {}, "items": [own_item(), "junk"]}).encode()
    install(Response(200, body, {"ETag": 'W/"2"'}))
    assert await svc.own_recipes_raw('W/"1"') == ([own_item()], 'W/"2"')


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (Response(401), "refused the login token"),
        (Response(302), "returned 302"),
        (Response(304), "returned 304"),  # a 304 without having sent an ETag
        (Response(200, b'{"items": 1}'), "unexpected custom recipe list"),
    ],
)
async def test_own_recipes_raw_errors(service, response, error):
    svc, install = service
    install(response)
    with pytest.raises(Exception, match=error):
        await svc.own_recipes_raw()


async def test_raw_json_is_size_limited(service, monkeypatch):
    import cookidoo_service

    monkeypatch.setattr(cookidoo_service, "MAX_RAW_JSON", 1000)
    svc, install = service
    install(Response(200, b"[" + b" " * 300_000 + b"]"))
    with pytest.raises(CookidooRequestException, match="too large"):
        await svc.catalogue_recipe_raw("r1")


async def test_catalogue_recipe_raw(service):
    svc, install = service
    session = install(Response(200, json.dumps(catalogue_raw("r1")).encode()))
    assert (await svc.catalogue_recipe_raw("r1"))["title"] == "Testrisotto"
    assert session.requests[0][0] == "https://cookidoo.example/recipes/recipe/de-CH/r1"
    with pytest.raises(ValueError):
        await svc.catalogue_recipe_raw("../x")


async def test_all_collections_reads_every_page(monkeypatch):
    from cookidoo_service import CookidooService, CookidooSettings

    svc = CookidooService(CookidooSettings(email="a@b.c", password="x"))
    pages = []

    async def list_collections(managed, page):
        pages.append((managed, page))
        return [collection(f"c{page}", "C", [])], 3, 3

    monkeypatch.setattr(svc, "list_collections", list_collections)
    assert [c.id for c in await svc.all_collections(managed=True)] == ["c0", "c1", "c2"]
    assert pages == [(True, 0), (True, 1), (True, 2)]
