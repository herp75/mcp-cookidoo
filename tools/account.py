"""Account tools: user, subscription, devices."""

from security import tool_error
from tools.common import READ_ONLY, one_line, service, tool


@tool(READ_ONLY)
async def get_account_info() -> str:
    """Show the Cookidoo account: user name, Cookidoo site, subscription and linked devices."""
    svc = service()
    try:
        user, subscription = await svc.get_account()
        devices = await svc.get_devices()
    except Exception as e:
        raise tool_error("Failed to get account info", e) from e

    lines = [
        f"User: {one_line(user.username)}",
        f"Site: {svc.localization_url} ({svc.settings.country}, {svc.settings.language})",
    ]
    if subscription is None:
        lines.append("Subscription: none active")
    else:
        lines += [
            f"Subscription: {one_line(subscription.subscription_level)} "
            f"({one_line(subscription.status)}, {'active' if subscription.active else 'inactive'})",
            f"Subscription expires: {one_line(subscription.expires) or '?'}",
        ]
    lines.append(f"Devices: {', '.join(d.type.value for d in devices) or 'none linked'}")
    device, source = await svc.resolve_device()
    lines.append(f"Device for new recipes: {device} ({source})")
    return "\n".join(lines)


@tool(READ_ONLY)
async def list_devices() -> str:
    """List the Thermomix devices linked to the Cookidoo account (e.g. TM6, TM7)."""
    try:
        devices = await service().get_devices()
    except Exception as e:
        raise tool_error("Failed to list devices", e) from e
    if not devices:
        return "No Thermomix linked to this account."
    return "\n".join(f"- {d.type.value}" for d in devices)
