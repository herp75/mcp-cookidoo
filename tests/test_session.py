"""Login and session lifecycle of CookidooService, including concurrent first calls."""

import asyncio
from unittest.mock import AsyncMock

import pytest

import cookidoo_service
from cookidoo_service import CookidooService, CookidooSettings


class Session:
    created: list["Session"] = []

    def __init__(self, *args, **kwargs):
        self.closed = False
        Session.created.append(self)

    async def close(self):
        self.closed = True


class Client:
    """A Cookidoo client whose login takes a moment and uses its session, like the real one."""

    fail = False
    logins = 0

    def __init__(self, session, cfg, on_auth_data_update=None):
        self.session = session

    async def login(self):
        Client.logins += 1
        await asyncio.sleep(0.01)
        if Client.fail:
            raise RuntimeError("login refused")

    async def call(self):
        await asyncio.sleep(0.01)
        if self.session.closed:
            raise RuntimeError("Session is closed")
        return "ok"


@pytest.fixture
def svc(monkeypatch):
    Session.created, Client.logins, Client.fail = [], 0, False
    monkeypatch.setattr(cookidoo_service, "ClientSession", Session)
    monkeypatch.setattr(cookidoo_service, "Cookidoo", Client)
    monkeypatch.setattr(
        cookidoo_service, "get_localization_options", AsyncMock(return_value=[object()])
    )
    return CookidooService(CookidooSettings(email="a@b.c", password="x"))


async def test_concurrent_first_calls_share_one_login(svc):
    async def tool_call():
        return await (await svc.client()).call()

    assert await asyncio.gather(*(tool_call() for _ in range(5))) == ["ok"] * 5
    assert Client.logins == 1
    assert len(Session.created) == 1 and not Session.created[0].closed


async def test_relogin_swaps_the_session_after_success(svc):
    first = await svc.client()
    second = await svc.login()
    assert second is not first and await svc.client() is second
    old, new = Session.created
    assert old.closed and not new.closed


async def test_failed_relogin_keeps_the_working_session(svc):
    first = await svc.client()
    Client.fail = True
    with pytest.raises(RuntimeError, match="login refused"):
        await svc.login()
    assert await svc.client() is first and await first.call() == "ok"
    old, failed = Session.created
    assert not old.closed and failed.closed


async def test_failed_first_login_leaves_no_session(svc):
    Client.fail = True
    with pytest.raises(RuntimeError):
        await svc.client()
    assert svc._api_client is None and svc._session is None
    assert Session.created[0].closed


async def test_close(svc):
    await svc.client()
    await svc.close()
    assert Session.created[0].closed and svc._api_client is None
