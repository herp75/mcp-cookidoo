# The mode limits, device rules and editor-page parser in this file are derived from
# tobilektri/mcp-cookidoo (https://github.com/tobilektri/mcp-cookidoo, mcp_cookidoo/modes.py
# and thermomix.py), Copyright (c) 2025 Alexandre Patelli, Copyright (c) 2026 Tobias Meindl,
# MIT License. See NOTICE. Adapted to this server's structured step format.
"""
Thermomix devices: which settings and modes each device supports, and with which values.

Two sources, as recorded from the cookidoo.de recipe editor (see docs/device-modes-analysis.md):

- MODE_RULES / manual limits: the limits per mode, from the editor's JavaScript
  (pl-customer-recipes 1.174.12, 2026-10-20). They are not on the editor page, so they are
  a snapshot that the scheduled live check flags for review when Cookidoo changes the editor.
- ModeCatalog: what the editor page offers right now (modes, TM7-only modes, value lists).
  Read live by CookidooService.get_mode_catalog(); BUILTIN_CATALOG is the fallback.

The catalog can only restrict the snapshot rules, never widen them: the page is untrusted
input, and only known names and values are taken from it.
"""

from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Literal

from schemas import (
    BlendSettings,
    BrowningSettings,
    DoughSettings,
    ManualSettings,
    RiceCookerSettings,
    SteamingSettings,
    Step,
    StepSettings,
    TurboSettings,
    WarmUpSettings,
)

Device = Literal["TM5", "TM6", "TM7"]
DEVICES: tuple[Device, ...] = ("TM5", "TM6", "TM7")
DEFAULT_DEVICE: Device = "TM6"

SPEEDS = ("soft", "0.5", "1", "1.5", "2", "2.5", "3", "3.5", "4", "4.5", "5",
          "5.5", "6", "6.5", "7", "7.5", "8", "8.5", "9", "9.5", "10")  # fmt: skip
TEMPERATURES = ("37", "40", "45", "50", "55", "60", "65", "70", "75", "80", "85", "90",
                "95", "98", "100", "105", "110", "115", "120",
                "140", "145", "150", "155", "160", "varoma")  # fmt: skip
TURBO_TIMES = (0.5, 1, 2)
PULSE_COUNTS = tuple(range(1, 10))
MODES = ("dough", "browning", "steaming", "turbo", "rice_cooker", "warm_up", "blend")

# Manual settings (time / temperature / speed)
MANUAL_MAX_TIME = 5940  # 99 min
MANUAL_MAX_HEATING_SPEED = 6.0
MANUAL_MAX_TEMPERATURE = 120
# Time grid of the editor: up to 5 min in 10 s steps, up to 20 min in 30 s, then minutes
TIME_GRID = ((300, 10), (1200, 30), (MANUAL_MAX_TIME, 60))


def _celsius(low: int, high: int) -> tuple[str, ...]:
    return tuple(t for t in TEMPERATURES if t.isdigit() and low <= int(t) <= high)


@dataclass(frozen=True)
class ModeRule:
    """Editor limits of one mode. None: no limit beyond the schema / the catalog."""

    devices: tuple[Device, ...] = ("TM6", "TM7")
    min_time: float | None = None
    max_time: float | None = None
    speeds: tuple[str, ...] | None = None
    temperatures: tuple[str, ...] | None = None


MODE_RULES: dict[str, ModeRule] = {
    "dough": ModeRule(min_time=1, max_time=1200),
    "turbo": ModeRule(),
    "blend": ModeRule(min_time=10, max_time=300, speeds=("6", "6.5", "7", "7.5", "8")),
    "warm_up": ModeRule(speeds=("soft", "1", "2"), temperatures=_celsius(37, 90)),
    "rice_cooker": ModeRule(),
    "steaming": ModeRule(devices=("TM7",), min_time=1, max_time=MANUAL_MAX_TIME),
    "browning": ModeRule(devices=("TM7",), min_time=1, max_time=1800),
}


@dataclass(frozen=True)
class ModeCatalog:
    """What the Cookidoo editor offers (Cookidoo's internal values)."""

    modes: frozenset[str]
    tm7_only: frozenset[str]
    speeds: tuple[str, ...]
    temperatures: tuple[str, ...]
    turbo_times: tuple[float, ...]
    pulse_counts: tuple[int, ...]
    source: Literal["cookidoo", "builtin"] = "builtin"
    note: str = ""

    def mode_devices(self, mode: str) -> tuple[Device, ...]:
        """Devices a mode can be used on: the snapshot rule, restricted by the catalog."""
        if mode not in self.modes or mode not in MODE_RULES:
            return ()
        devices = MODE_RULES[mode].devices
        return ("TM7",) if mode in self.tm7_only and "TM7" in devices else devices

    def available_modes(self, device: Device) -> list[str]:
        return [m for m in MODES if device in self.mode_devices(m)]


BUILTIN_CATALOG = ModeCatalog(
    modes=frozenset(MODES),
    tm7_only=frozenset({"browning", "steaming"}),
    speeds=SPEEDS,
    temperatures=TEMPERATURES,
    turbo_times=TURBO_TIMES,
    pulse_counts=PULSE_COUNTS,
    source="builtin",
    note="built-in list (cookidoo.de editor, 2026-10-20)",
)


def describe_time(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:g} s"
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes} min" + (f" {rest} s" if rest else "")


def _off_grid(seconds: float) -> bool:
    for limit, step in TIME_GRID:
        if seconds <= limit:
            return seconds > 60 and seconds % step != 0
    return True


def _values(values: tuple[str, ...]) -> str:
    return ", ".join(f'"{v}"' for v in values)


def check_settings(
    settings: StepSettings, device: Device, catalog: ModeCatalog = BUILTIN_CATALOG
) -> tuple[list[str], list[str]]:
    """(errors, warnings) for one setting on a device. Errors name the allowed values."""
    errors: list[str] = []
    warnings: list[str] = []

    def speed_ok(speed: str, allowed: tuple[str, ...] | None = None) -> None:
        values = tuple(s for s in (allowed or SPEEDS) if s in catalog.speeds)
        if speed not in values:
            errors.append(f"speed must be one of {_values(values)}")

    def temperature_ok(temperature: str, allowed: tuple[str, ...]) -> None:
        values = tuple(t for t in allowed if t in catalog.temperatures)
        if temperature not in values:
            errors.append(f"temperature must be one of {_values(values)}")

    if isinstance(settings, ManualSettings):
        if settings.time > MANUAL_MAX_TIME:
            errors.append(f"time must be at most {describe_time(MANUAL_MAX_TIME)}")
        elif _off_grid(settings.time):
            warnings.append(
                f"{describe_time(settings.time)} is not on the editor's time grid (up to 5 min "
                "in 10 s steps, up to 20 min in 30 s steps, then whole minutes)"
            )
        if settings.temperature is not None:
            allowed = _celsius(37, MANUAL_MAX_TEMPERATURE)
            if device == "TM6":
                allowed += ("varoma",)
            if settings.temperature == "varoma" and device != "TM6":
                hint = ' (use mode "steaming")' if device == "TM7" else ""
                errors.append(f'"varoma" as a temperature is only available on the TM6{hint}')
            else:
                temperature_ok(settings.temperature, allowed)
            if settings.speed != "soft" and float(settings.speed) > MANUAL_MAX_HEATING_SPEED:
                errors.append("with a temperature, speed must be at most 6")
        speed_ok(settings.speed)
        return errors, warnings

    mode = settings.mode
    devices = catalog.mode_devices(mode)
    if device not in devices:
        if mode not in catalog.modes:
            errors.append(f'Cookidoo no longer offers the "{mode}" mode')
        elif device == "TM5":
            errors.append("the TM5 supports manual settings only (time, temperature, speed)")
        else:
            available = ", ".join(f'"{m}"' for m in catalog.available_modes(device))
            errors.append(
                f'"{mode}" is not available on the {device} '
                f"(only on the {'/'.join(devices)}); modes for the {device}: {available}"
            )
        return errors, warnings

    rule = MODE_RULES[mode]
    time = getattr(settings, "time", None)
    if isinstance(settings, TurboSettings):
        if settings.time not in catalog.turbo_times:
            errors.append(f"time must be one of {', '.join(f'{t:g}' for t in catalog.turbo_times)}")
        if settings.pulses > 1:
            if device != "TM7":
                errors.append("turbo pulses are only available on the TM7; use 1 pulse")
            elif settings.pulses not in catalog.pulse_counts:
                counts = catalog.pulse_counts
                errors.append(f"pulses must be between {min(counts)} and {max(counts)}")
    elif time is not None and rule.min_time is not None and rule.max_time is not None:
        if not rule.min_time <= time <= rule.max_time:
            errors.append(
                f"time must be between {describe_time(rule.min_time)} and "
                f"{describe_time(rule.max_time)}"
            )
    match settings:
        case BlendSettings() | SteamingSettings() | WarmUpSettings():
            speed_ok(settings.speed, rule.speeds)
    match settings:
        case WarmUpSettings():
            temperature_ok(settings.temperature, rule.temperatures or ())
        case BrowningSettings():
            temperature_ok(settings.temperature, _celsius(140, 160))
        case DoughSettings() | RiceCookerSettings():
            pass
    return errors, warnings


def check_steps(
    steps: list[str | Step], device: Device, catalog: ModeCatalog = BUILTIN_CATALOG
) -> tuple[list[str], list[str]]:
    """(errors, warnings) of a recipe's steps on a device, prefixed with the step number."""
    errors: list[str] = []
    warnings: list[str] = []
    for number, step in enumerate(steps, 1):
        if isinstance(step, str):
            continue
        for index, settings in enumerate(step.settings, 1):
            where = f"Step {number}" + (f", setting {index}" if len(step.settings) > 1 else "")
            label = "manual" if isinstance(settings, ManualSettings) else settings.mode
            step_errors, step_warnings = check_settings(settings, device, catalog)
            errors += [f"{where} ({label}): {e}" for e in step_errors]
            warnings += [f"{where} ({label}): {w}" for w in step_warnings]
    return errors, warnings


# --- Editor page parser --------------------------------------------------------------------

_SECTIONS = {
    "cr-tts-mode", "cr-tts-speed", "cr-tts-temperature", "cr-tts-time", "cr-tts-pulse-count",
}  # fmt: skip
MAX_EDITOR_HTML = 2 * 1024 * 1024


class _EditorParser(HTMLParser):
    """Collects the radio values of the settings dialog and the TM7-only mode labels."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.section: str | None = None
        self.depth = 0
        self.values: dict[str, list[tuple[str, str]]] = {s: [] for s in _SECTIONS}
        self.tm7_labels: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {k: v or "" for k, v in attrs}
        if tag in _SECTIONS and self.section is None:
            self.section, self.depth = tag, 0
        elif tag == self.section:
            self.depth += 1
        elif tag == "input" and self.section and attributes.get("type") == "radio":
            self.values[self.section].append(
                (attributes.get("value", ""), attributes.get("id", ""))
            )
        elif tag == "label" and "tm7-mode" in attributes:
            self.tm7_labels.add(attributes.get("for", ""))

    def handle_endtag(self, tag: str) -> None:
        if tag == self.section:
            if self.depth:
                self.depth -= 1
            else:
                self.section = None


def parse_editor_html(html: str) -> ModeCatalog:
    """
    The mode catalog from a Cookidoo editor page. Only known names and in-range values are
    taken; anything else on the page is ignored.

    Raises:
        ValueError: if the page offers no known mode (e.g. a login or error page)
    """
    if len(html) > MAX_EDITOR_HTML:
        raise ValueError("editor page too large")
    parser = _EditorParser()
    parser.feed(html)
    parser.close()

    def known(section: str, allowed: tuple[str, ...]) -> tuple[str, ...]:
        found = {value for value, _ in parser.values[section]}
        return tuple(v for v in allowed if v in found)

    mode_ids = {value: id_ for value, id_ in parser.values["cr-tts-mode"]}
    modes = frozenset(m for m in MODES if m in mode_ids)
    if not modes:
        raise ValueError("the editor page offers no known Thermomix mode")

    def number(value: str) -> float | None:
        try:
            return float(value)
        except ValueError:
            return None

    times = {number(v) for v, _ in parser.values["cr-tts-time"]}
    pulses = {number(v) for v, _ in parser.values["cr-tts-pulse-count"]}
    return ModeCatalog(
        modes=modes,
        tm7_only=frozenset(m for m in modes if mode_ids[m] in parser.tm7_labels),
        speeds=known("cr-tts-speed", SPEEDS) or SPEEDS,
        temperatures=known("cr-tts-temperature", TEMPERATURES) or TEMPERATURES,
        turbo_times=tuple(t for t in TURBO_TIMES if t in times) or TURBO_TIMES,
        pulse_counts=tuple(p for p in PULSE_COUNTS if p in pulses) or PULSE_COUNTS,
        source="cookidoo",
        note="read from the Cookidoo recipe editor",
    )
