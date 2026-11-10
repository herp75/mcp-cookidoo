"""
Live tests against the real Cookidoo API, with a dedicated test account.

Skipped unless pytest runs with --live (read-only tests) or --live-write (also the tests that
create data, each removing what it created). Credentials come only from COOKIDOO_TEST_* in the
environment or the git-ignored .env.live, never from COOKIDOO_EMAIL / COOKIDOO_PASSWORD.

Run them as in the "Live tests" workflow, so that nothing from the account is printed:

    uv run pytest tests/live --live-write -p no:logging --assert=plain

Every tool output and every log record of the run is checked for the password and the OAuth
tokens. Everything the tests create is named with PREFIX, and leftovers of an earlier,
interrupted run are removed first.
"""

import logging
import os
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from dotenv import dotenv_values
from fastmcp import Client
from fastmcp.exceptions import ToolError

import server
from cookidoo_service import CookidooService, CookidooSettings
from security import register_secret

ROOT = Path(__file__).parents[2]
ENV_FILE = ROOT / ".env.live"
PREFIX = "MCP live test"
RUN = uuid.uuid4().hex[:8]


def test_settings() -> CookidooSettings:
    values = {k: v for k, v in dotenv_values(ENV_FILE).items() if v}
    values |= {k: v for k, v in os.environ.items() if k.startswith("COOKIDOO_TEST_") and v}
    email, password = values.get("COOKIDOO_TEST_EMAIL"), values.get("COOKIDOO_TEST_PASSWORD")
    if not email or not password:
        pytest.fail(
            "Set COOKIDOO_TEST_EMAIL and COOKIDOO_TEST_PASSWORD (environment or .env.live, "
            "see .env.live.example) for the live tests.",
            pytrace=False,
        )
    personal = os.environ.get("COOKIDOO_EMAIL") or dotenv_values(ROOT / ".env").get(
        "COOKIDOO_EMAIL"
    )
    if personal and personal.strip().lower() == email.strip().lower():
        pytest.fail(
            "COOKIDOO_TEST_EMAIL is the account in COOKIDOO_EMAIL: the live tests need a "
            "dedicated test account.",
            pytrace=False,
        )
    register_secret(password)
    register_secret(email)
    return CookidooSettings(
        email=email,
        password=password,
        country=values.get("COOKIDOO_TEST_COUNTRY", "ch").lower(),
        language=values.get("COOKIDOO_TEST_LANGUAGE", "de-CH"),
        device=None,
    )


test_settings.__test__ = False  # type: ignore[attr-defined]  # not a test, despite the name


class LiveService(CookidooService):
    """Keeps one login for the whole run: each in-memory MCP session would close it."""

    async def close(self) -> None:
        pass

    async def shutdown(self) -> None:
        await super().close()

    def secrets(self) -> list[str]:
        values = [self.settings.password]
        auth = self._api_client.auth_data if self._api_client else None
        if auth:
            values += [auth.access_token, auth.refresh_token]
        return [v for v in values if v]


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.messages.append(self.format(record))
        except Exception:
            self.messages.append(str(record.msg))


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def live(request: pytest.FixtureRequest) -> AsyncIterator[LiveService]:
    """The live service, installed as the server's service, logged in once."""
    write = request.config.getoption("--live-write")
    records = _Records()
    root = logging.getLogger()
    level = root.level
    root.addHandler(records)
    root.setLevel(logging.DEBUG)  # debug logs of aiohttp / cookidoo-api must not leak either
    svc = LiveService(test_settings())
    previous = server._cookidoo_service
    server._cookidoo_service = svc
    secrets: list[str] = []
    try:
        await svc.login()
        secrets = svc.secrets()
        if write:
            await _sweep(svc)
        yield svc
        secrets = svc.secrets()  # tokens may have been refreshed
        if write:
            await _sweep(svc)
    finally:
        server._cookidoo_service = previous
        await svc.shutdown()
        root.removeHandler(records)
        root.setLevel(level)
    leaked = sum(any(s in m for s in secrets) for m in records.messages)
    assert not leaked, f"{leaked} log record(s) contain the password or a token (not shown)"


async def _sweep(svc: CookidooService) -> None:
    """Remove what an earlier, interrupted run left on the test account."""
    for recipe in await svc.list_custom_recipes():
        if recipe.name.startswith(PREFIX):
            await svc.delete_custom_recipe(recipe.id)
    collections, _, pages = await svc.list_collections(managed=False, page=0)
    for page in range(1, pages):
        collections += (await svc.list_collections(managed=False, page=page))[0]
    for collection in collections:
        if collection.name.startswith(PREFIX):
            await svc.delete_collection(collection.id)
    _, _, extra = await svc.get_shopping_list()
    if old := [item.id for item in extra if item.name.startswith(PREFIX)]:
        await svc.remove_shopping_list_items(old)


async def call(live: LiveService, tool: str, **args: Any) -> str:
    """Call a tool through MCP; fail (without showing the output) if it contains a secret."""
    async with Client(server.mcp) as client:
        try:
            result = await client.call_tool(tool, args)
        except ToolError as e:
            _check(live, str(e), tool)
            raise
    text = "\n".join(getattr(block, "text", "") for block in result.content)
    _check(live, text, tool)
    return text


def _check(live: LiveService, text: str, tool: str) -> None:
    if any(secret in text for secret in live.secrets()):
        pytest.fail(f"{tool} returned the password or a token (output not shown)", pytrace=False)


def find_id(label: str, text: str) -> str:
    match = re.search(rf"{label}: ([A-Za-z0-9_-]+)", text)
    assert match, f"no '{label}' in the tool output"
    return match.group(1)


def name(what: str) -> str:
    return f"{PREFIX} {RUN} {what}"


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def catalog_recipe(live: LiveService) -> str:
    """ID of a catalogue recipe of the test account's site, found by search."""
    result = await live.search_recipes(query="Risotto", max_results=5)
    if not result.recipes:
        result = await live.search_recipes(max_results=5)
    assert result.recipes, "the search returned no recipe"
    return result.recipes[0].id
