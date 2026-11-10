"""
Compare the live Cookidoo recipe editor with the limits this server enforces.

Two sources, both read by the weekly live run:
- the editor page (HTML): modes, TM7-only marks, value lists, parsed by devices.py;
- the editor's script (pl-customer-recipes-<version>-<hash>.js, public, no login): the time
  ranges and value lists per mode, which devices.MODE_RULES copies.

Only the script on Vorwerk's pattern-library host is fetched; values are taken with narrow
regular expressions, so what ends up in a report is numbers and known value names.
"""

import re
from dataclasses import dataclass, field

from devices import BUILTIN_CATALOG, MANUAL_MAX_TIME, MODE_RULES, MODES, ModeCatalog

SCRIPT_URL = re.compile(
    r"https://patternlib-all\.prod\.external\.eu-tm-prod\.vorwerk-digital\.com/"
    r"pl-customer-recipes-(\d{1,4}\.\d{1,4}\.\d{1,4})-[0-9a-f]{32}\.js"
)
MAX_SCRIPT = 5 * 1024 * 1024
_VALUE = re.compile(r'"([A-Za-z0-9_.]{1,12})"')


@dataclass(frozen=True)
class Limits:
    time: tuple[int, int] | None = None
    speeds: tuple[str, ...] | None = None
    temperatures: tuple[str, ...] | None = None


@dataclass
class Report:
    site: str
    version: str | None
    differences: list[str] = field(default_factory=list)

    def markdown(self) -> str:
        lines = [
            f"The weekly live check found that the Cookidoo recipe editor ({self.site}, "
            f"pl-customer-recipes {self.version or 'unknown version'}) no longer matches the "
            "limits in `devices.py`:",
            "",
            *(f"- {d}" for d in self.differences),
            "",
            "Check the editor, then update `MODE_RULES` / `BUILTIN_CATALOG` in `devices.py`, "
            "`get_thermomix_modes`, the prompts and the README mode table.",
        ]
        return "\n".join(lines)


def script_url(html: str) -> tuple[str, str] | None:
    """URL and version of the editor script linked from the editor page."""
    match = SCRIPT_URL.search(html)
    return (match.group(0), match.group(1)) if match else None


def _values(text: str) -> tuple[str, ...]:
    return tuple(_VALUE.findall(text))


def _limits(block: str) -> Limits:
    time = re.search(r"minTime:(\d{1,5}),maxTime:(\d{1,5})", block)
    speeds = re.search(r"availableSpeeds:\[([^\]]{0,400})\]", block)
    # Celsius list first: availableTemperatures:this...unit==="C"?[celsius]:[fahrenheit]
    temperatures = re.search(r"availableTemperatures:[^\[]{0,80}\[([^\]]{0,400})\]", block)
    return Limits(
        time=(int(time.group(1)), int(time.group(2))) if time else None,
        speeds=_values(speeds.group(1)) if speeds else None,
        temperatures=_values(temperatures.group(1)) if temperatures else None,
    )


def script_limits(js: str) -> dict[str, Limits]:
    """Limits per mode (and "manual") from the editor script; modes not found are absent."""
    found: dict[str, Limits] = {}
    for mode in MODES:
        start = js.find(f'mode:"{mode}"}})')
        if start >= 0:
            end = js.find("disable()", start)
            found[mode] = _limits(js[start : end if end > 0 else start + 2000])
    manual = re.search(r"showTimeSection\(e\)\{return this\.timeSection\.show\(e,\{[^}]{0,200}", js)
    if manual:
        found["manual"] = _limits(manual.group(0))
    return found


def expected_limits() -> dict[str, Limits]:
    """What devices.py enforces, in the same shape."""
    expected = {
        mode: Limits(
            time=(int(rule.min_time), int(rule.max_time))
            if rule.min_time is not None and rule.max_time is not None
            else None,
            speeds=rule.speeds,
            temperatures=rule.temperatures,
        )
        for mode, rule in MODE_RULES.items()
    }
    expected["manual"] = Limits(time=(1, MANUAL_MAX_TIME))
    return expected


def compare_script(found: dict[str, Limits]) -> list[str]:
    differences = []
    for mode, want in expected_limits().items():
        got = found.get(mode)
        if got is None:
            if mode not in ("turbo", "rice_cooker"):  # no limits of their own in the script
                differences.append(f"`{mode}`: not found in the editor script")
            continue
        if got.time != want.time:
            differences.append(f"`{mode}` time: editor {got.time}, devices.py {want.time}")
        if want.speeds is not None and got.speeds != want.speeds:
            differences.append(f"`{mode}` speeds: editor {got.speeds}, devices.py {want.speeds}")
        if want.temperatures is not None and got.temperatures != want.temperatures:
            differences.append(
                f"`{mode}` temperatures: editor {got.temperatures}, devices.py {want.temperatures}"
            )
    return differences


def compare_catalog(live: ModeCatalog, builtin: ModeCatalog = BUILTIN_CATALOG) -> list[str]:
    differences = []
    for name in ("modes", "tm7_only", "speeds", "temperatures", "turbo_times", "pulse_counts"):
        got, want = getattr(live, name), getattr(builtin, name)
        if got != want:
            if isinstance(got, frozenset):
                got, want = sorted(got), sorted(want)
            differences.append(f"editor page `{name}`: {list(got)}, built-in {list(want)}")
    return differences
