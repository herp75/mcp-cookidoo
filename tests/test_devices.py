"""Device rules (TM5/TM6/TM7), the editor mode catalog, and device-aware recipe tools."""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from cookidoo_api import Cookidoo
from fastmcp import Client
from fastmcp.exceptions import ToolError
from pydantic import TypeAdapter

import cookidoo_service
import devices
import server
from devices import (
    BUILTIN_CATALOG,
    MODES,
    check_settings,
    check_steps,
    parse_editor_html,
)
from guided import to_instruction
from schemas import Step, StepSettings
from tests.conftest import SECRET, run

EDITOR = Path(__file__).parent / "fixtures" / "editor"
SETTINGS = TypeAdapter(StepSettings)

# A valid setting of every mode, for the device matrix
VALID = {
    "manual": {"time": 180, "temperature": "100", "speed": "1"},
    "dough": {"mode": "dough", "time": 120},
    "browning": {"mode": "browning", "time": 600, "temperature": "160", "power": "gentle"},
    "steaming": {"mode": "steaming", "time": 1200, "speed": "1"},
    "turbo": {"mode": "turbo", "time": 1},
    "rice_cooker": {"mode": "rice_cooker"},
    "warm_up": {"mode": "warm_up", "temperature": "65", "speed": "soft"},
    "blend": {"mode": "blend", "time": 60, "speed": "8"},
}
SUPPORTED = {
    "TM5": {"manual"},
    "TM6": {"manual", "dough", "turbo", "rice_cooker", "warm_up", "blend"},
    "TM7": set(VALID),
}


def errors(settings: dict, device: str, catalog=BUILTIN_CATALOG) -> list[str]:
    return check_settings(SETTINGS.validate_python(settings), device, catalog)[0]  # type: ignore[arg-type]


# --- Device matrix ---------------------------------------------------------------------------


@pytest.mark.parametrize("device", ["TM5", "TM6", "TM7"])
@pytest.mark.parametrize("mode", list(VALID))
def test_every_mode_on_every_device(mode, device):
    found = errors(VALID[mode], device)
    if mode in SUPPORTED[device]:
        assert found == []
    else:
        assert len(found) == 1
        assert "TM5 supports manual settings only" in found[0] or (
            f'"{mode}" is not available on the {device} (only on the TM7)' in found[0]
        )


def test_unavailable_mode_lists_the_modes_of_the_device():
    (message,) = errors(VALID["browning"], "TM6")
    assert message.endswith(
        'modes for the TM6: "dough", "turbo", "rice_cooker", "warm_up", "blend"'
    )


# --- Limits ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("settings", "device", "message"),
    [
        ({"time": 6000, "speed": "1"}, "TM6", "time must be at most 99 min"),
        ({"time": 60, "temperature": "100", "speed": "7"}, "TM6", "at most 6"),
        ({"time": 60, "temperature": "varoma", "speed": "1"}, "TM7", 'use mode "steaming"'),
        ({"time": 60, "temperature": "varoma", "speed": "1"}, "TM5", "only available on the TM6"),
        ({"mode": "dough", "time": 1800}, "TM6", "time must be between 1 s and 20 min"),
        ({"mode": "blend", "time": 60, "speed": "10"}, "TM6", '"6", "6.5", "7", "7.5", "8"'),
        ({"mode": "blend", "time": 5, "speed": "8"}, "TM7", "between 10 s and 5 min"),
        ({"mode": "blend", "time": 600, "speed": "8"}, "TM6", "between 10 s and 5 min"),
        ({"mode": "warm_up", "temperature": "95", "speed": "1"}, "TM6", '"37", "40"'),
        ({"mode": "warm_up", "temperature": "65", "speed": "3"}, "TM6", '"soft", "1", "2"'),
        ({"mode": "turbo", "time": 1, "pulses": 3}, "TM6", "only available on the TM7"),
        ({"mode": "steaming", "time": 6000, "speed": "1"}, "TM7", "between 1 s and 99 min"),
    ],
)
def test_limits_name_the_allowed_values(settings, device, message):
    found = errors(settings, device)
    assert any(message in e for e in found), found


@pytest.mark.parametrize(
    ("settings", "device"),
    [
        ({"time": 5940, "temperature": "120", "speed": "6"}, "TM6"),  # 99 min, heat, speed 6
        ({"time": 60, "speed": "10"}, "TM7"),  # high speed without heat
        ({"time": 60, "temperature": "100", "speed": "soft"}, "TM7"),
        ({"time": 1200, "temperature": "varoma", "speed": "1", "reverse": True}, "TM6"),
        ({"time": 60, "temperature": "90", "speed": "2"}, "TM5"),
        ({"mode": "turbo", "time": 0.5, "pulses": 9}, "TM7"),
        ({"mode": "turbo", "time": 2, "pulses": 1}, "TM6"),
        ({"mode": "blend", "time": 10, "speed": "6"}, "TM6"),
        ({"mode": "dough", "time": 1200}, "TM6"),
        ({"mode": "warm_up", "temperature": "90", "speed": "2"}, "TM7"),
        ({"mode": "browning", "time": 1800, "temperature": "140", "power": "intense"}, "TM7"),
        ({"mode": "steaming", "time": 5940, "speed": "5", "reverse": True}, "TM7"),
    ],
)
def test_values_at_the_limits_are_accepted(settings, device):
    assert errors(settings, device) == []


def test_off_grid_time_is_a_warning_only():
    settings = SETTINGS.validate_python({"time": 307, "speed": "1"})
    found, warnings = check_settings(settings, "TM6")
    assert found == [] and "not on the editor's time grid" in warnings[0]
    assert check_settings(SETTINGS.validate_python({"time": 45, "speed": "1"}), "TM6") == ([], [])


def test_check_steps_numbers_steps_and_settings():
    steps = [
        "Preheat the oven.",
        Step(text="Blend {settings}.", settings=[VALID["blend"]]),
        Step(
            text="Chop {settings}, then {settings}.",
            settings=[{"time": 5, "speed": "5"}, {"mode": "browning", **VALID["browning"]}],
        ),
    ]
    found, _ = check_steps(steps, "TM6")
    assert found == [
        'Step 3, setting 2 (browning): "browning" is not available on the TM6 (only on the '
        'TM7); modes for the TM6: "dough", "turbo", "rice_cooker", "warm_up", "blend"'
    ]
    assert check_steps(steps, "TM7") == ([], [])


# --- The catalog restricts, never widens -----------------------------------------------------


def test_catalog_without_a_mode_refuses_it():
    catalog = replace(BUILTIN_CATALOG, modes=BUILTIN_CATALOG.modes - {"blend"})
    assert errors(VALID["blend"], "TM6", catalog) == ['Cookidoo no longer offers the "blend" mode']


def test_catalog_can_make_a_mode_tm7_only():
    catalog = replace(BUILTIN_CATALOG, tm7_only=frozenset({"dough", "browning", "steaming"}))
    assert errors(VALID["dough"], "TM6", catalog)
    assert errors(VALID["dough"], "TM7", catalog) == []


def test_catalog_cannot_widen_the_rules():
    catalog = replace(BUILTIN_CATALOG, tm7_only=frozenset())  # page claims nothing is TM7-only
    assert errors(VALID["browning"], "TM6", catalog)
    assert errors(VALID["steaming"], "TM6", catalog)


def test_catalog_value_lists_restrict():
    catalog = replace(BUILTIN_CATALOG, turbo_times=(1,), pulse_counts=(1, 2, 3))
    assert "time must be one of 1" in errors({"mode": "turbo", "time": 0.5}, "TM6", catalog)[0]
    assert "between 1 and 3" in errors({"mode": "turbo", "time": 1, "pulses": 5}, "TM7", catalog)[0]


# --- Editor page parser ----------------------------------------------------------------------


@pytest.mark.parametrize("name", ["editor_tm6.html", "editor_tm7.html"])
def test_parse_editor_snapshots(name):
    catalog = parse_editor_html((EDITOR / name).read_text(encoding="utf-8"))
    assert catalog.source == "cookidoo"
    assert catalog.modes == frozenset(MODES)
    assert catalog.tm7_only == {"browning", "steaming"}
    assert catalog.speeds == BUILTIN_CATALOG.speeds
    assert catalog.temperatures == BUILTIN_CATALOG.temperatures
    assert catalog.turbo_times == (0.5, 1, 2) and catalog.pulse_counts == tuple(range(1, 10))


@pytest.mark.parametrize(
    "html",
    [
        "",
        "<html><body><form>Please log in</form></body></html>",
        "<cr-tts-mode><input type='radio' value='sous_vide' id='m1'></cr-tts-mode>",
        "<<<>>> not html at all &&&",
    ],
)
def test_pages_without_known_modes_are_rejected(html):
    with pytest.raises(ValueError):
        parse_editor_html(html)


def test_unknown_modes_and_values_are_ignored():
    html = """
    <cr-tts-mode>
      <input type="radio" value="dough" id="m-dough">
      <input type="radio" value="sous_vide" id="m-sv"><label tm7-mode for="m-sv"></label>
      <input type="radio" value="turbo" id="m-turbo">
    </cr-tts-mode>
    <cr-tts-speed>
      <input type="radio" value="4"><input type="radio" value="99"><input type="radio" value="x">
    </cr-tts-speed>
    <cr-tts-time><input type="radio" value="1"><input type="radio" value="30"></cr-tts-time>
    <cr-tts-pulse-count><input type="radio" value="-1"><input type="radio" value="3">
    </cr-tts-pulse-count>
    """
    catalog = parse_editor_html(html)
    assert catalog.modes == {"dough", "turbo"} and catalog.tm7_only == frozenset()
    assert catalog.speeds == ("4",)
    assert catalog.turbo_times == (1,) and catalog.pulse_counts == (3,)


def test_script_and_malformed_markup_cannot_inject_modes():
    html = """
    <cr-tts-mode><input type="radio" value="dough" id="d">
      <script>document.write('<input type="radio" value="browning" id="b">')</script>
      <!-- <input type="radio" value="steaming" id="s"> -->
      <div><span>unclosed
    </cr-tts-mode>
    <input type="radio" value="blend" id="outside-any-section">
    """
    catalog = parse_editor_html(html)
    assert catalog.modes == {"dough"}


def test_missing_value_lists_fall_back_to_the_builtin_ones():
    catalog = parse_editor_html('<cr-tts-mode><input type="radio" value="turbo"></cr-tts-mode>')
    assert catalog.speeds == BUILTIN_CATALOG.speeds
    assert catalog.turbo_times == BUILTIN_CATALOG.turbo_times


def test_oversized_page_is_rejected():
    with pytest.raises(ValueError, match="too large"):
        parse_editor_html("x" * (2 * 1024 * 1024 + 1))


# --- Same annotation data as the Cookidoo editor ---------------------------------------------

# Our structured form of each step the editor built (tests/fixtures/editor/editor_modes.json)
EDITOR_CASES = [
    {"mode": "dough", "time": 120},
    {"mode": "turbo", "time": 1},
    {"mode": "blend", "time": 60, "speed": "8"},
    {"mode": "warm_up", "temperature": "65", "speed": "1"},
    {"mode": "rice_cooker"},
    {"time": 1200, "temperature": "varoma", "speed": "1", "reverse": True},
    {
        "mode": "steaming",
        "time": 1200,
        "speed": "1",
        "reverse": True,
        "accessory": "simmering_basket",
    },
    {"mode": "browning", "time": 600, "temperature": "160", "power": "intense"},
    {"mode": "turbo", "time": 1, "pulses": 3},
    {"mode": "warm_up", "temperature": "70", "speed": "soft"},
]


def wire(settings: dict) -> Any:
    step = Step(text="X {settings}.", settings=[settings])
    return Cookidoo._process_recipe_steps([to_instruction(step, "de-DE")], [])[0]["annotations"][0]


def test_editor_fixture_has_a_case_for_each_entry():
    cases = json.loads((EDITOR / "editor_modes.json").read_text(encoding="utf-8"))["cases"]
    assert len(cases) == len(EDITOR_CASES)


@pytest.mark.parametrize("index", range(len(EDITOR_CASES)))
def test_annotation_data_matches_the_editor(index):
    case = json.loads((EDITOR / "editor_modes.json").read_text(encoding="utf-8"))["cases"][index]
    (expected,) = case["expected"]["annotations"]
    ours = wire(EDITOR_CASES[index])
    assert ours["type"] == expected["type"]
    assert ours.get("name") == expected.get("name")
    data = expected["data"]
    if data.get("temperature", {}).get("value") == "varoma":
        # cookidoo-api drops the unit for Varoma (Cookidoo accepts both forms)
        data = {**data, "temperature": {"value": "varoma"}}
    assert ours["data"] == data
    assert errors(EDITOR_CASES[index], case["device"]) == []  # and we accept it for that device


def test_single_turbo_pulse_sends_no_pulse_count():
    assert wire({"mode": "turbo", "time": 0.5})["data"] == {"time": 0.5}
    assert wire({"mode": "turbo", "time": 1, "pulses": 2})["data"] == {"time": 1, "pulseCount": 2}


def test_varoma_unit_is_dropped_by_cookidoo_api():
    """Documents the library's behaviour: if it starts sending the unit, this test tells us."""
    temperature = wire({"time": 60, "temperature": "varoma", "speed": "1"})["data"]["temperature"]
    assert temperature == {"value": "varoma"}


# --- Device resolution and live catalog ------------------------------------------------------


@pytest.fixture
def svc(fake):
    return server._cookidoo_service


async def test_resolve_explicit_device(svc):
    assert await svc.resolve_device("TM7") == ("TM7", "given")


async def test_resolve_from_recipe_tools(svc, monkeypatch):
    async def recipe(recipe_id):
        return SimpleNamespace(tools=["TM7"]), []

    monkeypatch.setattr(svc, "get_custom_recipe", recipe)
    assert await svc.resolve_device(None, "01ABC") == ("TM7", "the recipe's devices")


async def test_resolve_from_setting(svc, monkeypatch):
    monkeypatch.setattr(svc, "settings", replace(svc.settings, device="TM5"))
    assert await svc.resolve_device() == ("TM5", "COOKIDOO_DEVICE")


@pytest.mark.parametrize(
    ("linked", "expected"),
    [(["TM6", "TM7"], "TM6"), (["TM7"], "TM7"), (["TM5"], "TM5"), ([], "TM6")],
)
async def test_resolve_from_linked_devices(svc, fake, linked, expected):
    from cookidoo_api import ThermomixMachineType
    from cookidoo_api.types import CookidooDevice

    fake.devices = [CookidooDevice(type=ThermomixMachineType(d)) for d in linked]
    device, _ = await svc.resolve_device()
    assert device == expected


async def test_resolve_falls_back_to_tm6_on_errors(svc, fake):
    fake.failures["get_devices"] = RuntimeError("down")
    assert await svc.resolve_device() == ("TM6", "default (linked devices could not be read)")


class FakeHTMLResponse:
    """Sends the body in small chunks, as the network does: one read() is not the page."""

    def __init__(self, status: int, body: str, chunk: int = 1000):
        self.status = status
        self.content = SimpleNamespace(iter_chunked=self._chunks)
        self._body, self._chunk = body.encode(), chunk
        self.chunks_read = 0

    async def _chunks(self, size: int):
        step = min(size, self._chunk)
        for start in range(0, len(self._body), step):
            self.chunks_read += 1
            yield self._body[start : start + step]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeHTMLSession:
    def __init__(self, response):
        self.response, self.requests = response, []

    def get(self, url, **kwargs):
        self.requests.append((str(url), kwargs))
        return self.response

    async def close(self):
        pass


@pytest.fixture
def editor_page(svc, monkeypatch):
    def install(status=200, body=None):
        html = (EDITOR / "editor_tm7.html").read_text(encoding="utf-8") if body is None else body
        session = FakeHTMLSession(FakeHTMLResponse(status, html))
        monkeypatch.setattr(svc, "_session", session)
        return session

    return install


async def test_catalog_is_read_from_the_editor_page_and_cached(svc, editor_page):
    session = editor_page()
    catalog = await svc.get_mode_catalog()
    assert catalog.source == "cookidoo" and catalog.tm7_only == {"browning", "steaming"}
    ((url, kwargs),) = session.requests
    assert url == (
        "https://cookidoo.example/created-recipes/en-GB/01TESTRECIPE00000000000001"
        "/edit/ingredients-and-preparation-steps"
    )
    assert kwargs["allow_redirects"] is False
    assert await svc.get_mode_catalog() is catalog and len(session.requests) == 1


@pytest.mark.parametrize(
    ("status", "body", "note"),
    [
        (302, "", "editor page returned 302"),
        (200, "<html>login</html>", "offers no known Thermomix mode"),
    ],
)
async def test_catalog_falls_back_on_bad_pages(svc, editor_page, status, body, note):
    editor_page(status, body)
    catalog = await svc.get_mode_catalog()
    assert catalog.source == "builtin" and note in catalog.note


async def test_catalog_fallback_is_reused_for_a_while(svc, editor_page, monkeypatch):
    session = editor_page(302, "")
    first = await svc.get_mode_catalog()
    assert await svc.get_mode_catalog() is first and len(session.requests) == 1
    clock = cookidoo_service.time.monotonic() + cookidoo_service.FALLBACK_TTL + 1
    monkeypatch.setattr(cookidoo_service.time, "monotonic", lambda: clock)
    assert (await svc.get_mode_catalog()).source == "builtin" and len(session.requests) == 2


async def test_catalog_stops_reading_oversized_pages(svc, editor_page, monkeypatch):
    monkeypatch.setattr(cookidoo_service, "MAX_EDITOR_HTML", 5000)
    monkeypatch.setattr(devices, "MAX_EDITOR_HTML", 5000)
    session = editor_page(body="<p>" + "x" * 50_000)
    catalog = await svc.get_mode_catalog()
    assert catalog.source == "builtin" and "too large" in catalog.note
    assert session.response.chunks_read <= 6


async def test_catalog_without_own_recipes(svc, fake, editor_page):
    session = editor_page()
    fake.own_recipe_ids = []
    catalog = await svc.get_mode_catalog()
    assert catalog.source == "builtin" and "no own recipe yet" in catalog.note
    assert session.requests == []


async def test_catalog_errors_are_redacted(svc, fake, editor_page):
    editor_page()
    fake.failures["list_custom_recipes"] = RuntimeError(f"password={SECRET}")
    catalog = await svc.get_mode_catalog()
    assert catalog.source == "builtin" and SECRET not in catalog.note


# --- Tools -----------------------------------------------------------------------------------

BROWNING_STEP = {"text": "Brown the onions {settings}.", "settings": [VALID["browning"]]}


async def generate(**args):
    async with Client(server.mcp) as client:
        return await client.call_tool(
            "generate_recipe_structure",
            {"name": "Onions", "ingredients": ["2 onions"], "steps": [BROWNING_STEP], **args},
        )


async def test_generate_refuses_modes_the_device_lacks(fake, editor_page):
    editor_page()
    with pytest.raises(ToolError) as error:
        await generate(device="TM6")
    message = str(error.value)
    assert message.startswith("Not valid for the TM6 (device: given):")
    assert '- Step 1 (browning): "browning" is not available on the TM6' in message
    assert "get_thermomix_modes" in message


async def test_generate_for_tm7_returns_json_then_notes(fake, editor_page):
    editor_page()
    result = await generate(device="TM7")
    recipe = json.loads(result.content[0].text)  # the JSON block is unchanged
    assert recipe["steps"][0]["settings"][0]["mode"] == "browning"
    notes = result.content[1].text
    assert notes.startswith("Checked for the TM7 (device: given; modes: read from the Cookidoo")


async def test_generate_refuses_what_the_upload_would(fake, editor_page):
    """The step is rendered as for the upload: a setting written twice is refused up front."""
    editor_page()
    step = {"text": "Rice cooker {settings}.", "settings": [{"mode": "rice_cooker"}]}
    with pytest.raises(ToolError) as error:
        await generate(device="TM6", steps=[step])
    assert "- Step 1: 'Rice cooker' is written twice in one step" in str(error.value)


async def test_generate_works_offline_with_builtin_rules(monkeypatch):
    monkeypatch.setattr(server, "_cookidoo_service", None)
    monkeypatch.setattr(
        server, "load_cookidoo_settings", lambda: (_ for _ in ()).throw(ValueError("no creds"))
    )
    result = await generate(device="TM7")
    assert "default" not in result.content[1].text and "built-in list" in result.content[1].text
    with pytest.raises(ToolError, match="Not valid for the TM6 \\(device: default \\(Cookidoo not"):
        await generate()


async def test_upload_creates_the_recipe_for_the_resolved_device(fake, editor_page, monkeypatch):
    editor_page()
    created = []

    async def create(recipe, device):
        created.append(device)
        return SimpleNamespace(name=recipe.name, id="01NEW", url="https://cookidoo.example/x")

    monkeypatch.setattr(server._cookidoo_service, "create_custom_recipe", create)
    recipe_json = (await generate(device="TM7")).content[0].text
    text = await run("upload_custom_recipe", recipe_json=recipe_json, device="TM7")
    assert text.startswith("Recipe 'Onions' created for the TM7.") and created == ["TM7"]
    with pytest.raises(ToolError, match="Not valid for the TM6"):
        await run("upload_custom_recipe", recipe_json=recipe_json)  # linked TM6+TM7 → TM6
    assert created == ["TM7"]


async def test_update_checks_against_the_recipe_devices(fake, editor_page, monkeypatch):
    editor_page()
    svc = server._cookidoo_service

    async def recipe(recipe_id):
        return SimpleNamespace(tools=["TM7"]), []

    async def update(recipe_id, recipe):
        return SimpleNamespace(name=recipe.name, id=recipe_id, url="https://cookidoo.example/x")

    monkeypatch.setattr(svc, "get_custom_recipe", recipe)
    monkeypatch.setattr(svc, "update_custom_recipe", update)
    recipe_json = (await generate(device="TM7")).content[0].text
    text = await run("update_custom_recipe", recipe_id="01ABC", recipe_json=recipe_json)
    assert "Checked for the TM7 (device: the recipe's devices" in text
    with pytest.raises(ToolError, match="Not valid for the TM6 \\(device: given\\)"):
        await run("update_custom_recipe", recipe_id="01ABC", recipe_json=recipe_json, device="TM6")


@pytest.mark.parametrize(
    ("device", "present", "absent"),
    [
        (
            "TM6",
            ["- blend: time 10 s to 5 min; speed 6, 6.5, 7, 7.5, 8", '"varoma" (TM6 only)'],
            ["- browning:", "pulses 1 to 9"],
        ),
        (
            "TM7",
            ["- browning: time 1 s to 30 min", "pulses 1 to 9", "- steaming:"],
            ['"varoma" (TM6 only)'],
        ),
        ("TM5", ["Manual settings", "- dough (only on the TM6/TM7)"], ["Modes on the TM5"]),
    ],
)
async def test_get_thermomix_modes(fake, editor_page, device, present, absent):
    editor_page()
    text = await run("get_thermomix_modes", device=device)
    assert text.startswith(f"Thermomix {device} (device: given; modes: read from the Cookidoo")
    for line in present:
        assert line in text, line
    for line in absent:
        assert line not in text, line


async def test_get_thermomix_modes_default_device(fake, editor_page):
    editor_page()
    text = await run("get_thermomix_modes")
    assert text.startswith("Thermomix TM6 (device: the devices linked to the account;")
