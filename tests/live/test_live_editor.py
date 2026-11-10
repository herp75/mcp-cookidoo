"""
Weekly check that the Cookidoo recipe editor still matches devices.py (analysis §7a).

On a difference the test fails and, with COOKIDOO_EDITOR_REPORT set (the "Live tests"
workflow), writes a Markdown report there, which the workflow posts as an issue.
"""

import os
from pathlib import Path

import pytest
from aiohttp import ClientSession, ClientTimeout

from devices import parse_editor_html
from schemas import CustomRecipe
from tests.live import editor_check
from tests.live.conftest import LiveService, name

pytestmark = [pytest.mark.live, pytest.mark.live_write, pytest.mark.asyncio(loop_scope="session")]


async def _script(url: str) -> str:
    """The public editor script: a separate session, without the Cookidoo login."""
    async with (
        ClientSession() as session,
        session.get(url, timeout=ClientTimeout(total=60), allow_redirects=False) as response,
    ):
        assert response.status == 200, f"editor script returned {response.status}"
        body = bytearray()
        async for chunk in response.content.iter_chunked(64 * 1024):
            body += chunk
            assert len(body) <= editor_check.MAX_SCRIPT, "editor script too large"
    return body.decode("utf-8", errors="replace")


def _write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


async def test_editor_matches_devices_py(live: LiveService):
    created = await live.create_custom_recipe(
        CustomRecipe(
            name=name("editor"),
            ingredients=["1 Ei"],
            steps=["Kochen."],
            servings=1,
            prep_time=1,
            total_time=1,
        ),
        "TM6",
    )
    try:
        html = await live.editor_html(created.id)
    finally:
        await live.delete_custom_recipe(created.id)

    site = f"{live.localization_url or '?'}"
    script = editor_check.script_url(html)
    report = editor_check.Report(site=site, version=script[1] if script else None)
    try:
        report.differences += editor_check.compare_catalog(parse_editor_html(html))
    except ValueError as e:
        report.differences.append(f"editor page could not be parsed ({e})")
    if script is None:
        report.differences.append("no pl-customer-recipes script found on the editor page")
    else:
        report.differences += editor_check.compare_script(
            editor_check.script_limits(await _script(script[0]))
        )

    print(f"Cookidoo editor: {site}, pl-customer-recipes {report.version}")
    if report.differences and (path := os.environ.get("COOKIDOO_EDITOR_REPORT")):
        _write(path, report.markdown())
    assert not report.differences, "\n".join(report.differences)
