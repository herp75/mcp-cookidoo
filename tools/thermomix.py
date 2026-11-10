"""Thermomix modes and limits per device."""

from devices import (
    BUILTIN_CATALOG,
    DEFAULT_DEVICE,
    MANUAL_MAX_HEATING_SPEED,
    MANUAL_MAX_TIME,
    MODE_RULES,
    MODES,
    Device,
    describe_time,
)
from tools.common import READ_ONLY, tool

_EXAMPLES = {
    "dough": '{"mode": "dough", "time": 120}',
    "browning": '{"mode": "browning", "time": 600, "temperature": "160", "power": "gentle"}',
    "steaming": '{"mode": "steaming", "time": 1200, "speed": "1", "accessory": "varoma"}',
    "turbo": '{"mode": "turbo", "time": 1}',
    "rice_cooker": '{"mode": "rice_cooker"}',
    "warm_up": '{"mode": "warm_up", "temperature": "65", "speed": "soft"}',
    "blend": '{"mode": "blend", "time": 60, "speed": "8"}',
}


def _limits(mode: str, device: Device) -> str:
    rule = MODE_RULES[mode]
    parts = []
    if mode == "turbo":
        parts.append("time 0.5, 1 or 2 s")
        parts.append("pulses 1 to 9" if device == "TM7" else "1 pulse (pulses: TM7 only)")
    elif rule.min_time is not None and rule.max_time is not None:
        parts.append(f"time {describe_time(rule.min_time)} to {describe_time(rule.max_time)}")
    if mode == "browning":
        parts.append("temperature 140 to 160 °C, power gentle or intense")
    if mode == "steaming":
        parts.append(
            'speed soft to 5, reverse, accessory "varoma", "simmering_basket" or '
            '"varoma_and_simmering_basket"'
        )
    if rule.speeds:
        parts.append(f"speed {', '.join(rule.speeds)}")
    if rule.temperatures:
        parts.append(f"temperature {rule.temperatures[0]} to {rule.temperatures[-1]} °C, no time")
    return "; ".join(parts) or "no settings"


@tool(READ_ONLY)
async def get_thermomix_modes(device: Device | None = None) -> str:
    """
    List the Thermomix settings and modes a device supports, with their limits and an example
    of each, as accepted by generate_recipe_structure. Call it before writing a recipe that
    uses modes. Modes are read from the Cookidoo recipe editor when possible.

    Args:
        device: "TM5", "TM6" or "TM7". Default: as for generate_recipe_structure
    """
    import server  # late import: server registers this module

    try:
        service = server._get_service()
    except Exception:
        service = None
    if service is None:
        resolved: Device = device or DEFAULT_DEVICE
        source = "given" if device else "default (Cookidoo not configured)"
        catalog = BUILTIN_CATALOG
    else:
        resolved, source = await service.resolve_device(device)
        catalog = await service.get_mode_catalog()

    varoma = ', or "varoma" (TM6 only)' if resolved == "TM6" else ""
    lines = [
        f"Thermomix {resolved} (device: {source}; modes: {catalog.note})",
        "",
        "Manual settings (no mode): "
        f"time 1 s to {describe_time(MANUAL_MAX_TIME)}; temperature 37 to 120 °C{varoma}, "
        f"or none; speed soft or 0.5 to 10, at most {MANUAL_MAX_HEATING_SPEED:g} with a "
        "temperature; reverse.",
        '  Example: {"time": 180, "temperature": "120", "speed": "1", "reverse": true}',
    ]
    available = catalog.available_modes(resolved)
    if available:
        lines += ["", f"Modes on the {resolved}:"]
        for mode in available:
            lines += [f"- {mode}: {_limits(mode, resolved)}", f"  Example: {_EXAMPLES[mode]}"]
    unavailable = [m for m in MODES if m not in available]
    if unavailable:
        lines += ["", f"Not available on the {resolved}:"]
        for mode in unavailable:
            devices = catalog.mode_devices(mode)
            where = f"only on the {'/'.join(devices)}" if devices else "not offered by Cookidoo"
            lines.append(f"- {mode} ({where})")
    return "\n".join(lines)
