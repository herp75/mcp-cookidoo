"""Login and session lifecycle of CookidooService, including concurrent first calls."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from cookidoo_api import CookidooAuthException

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

    fail: BaseException | None = None
    logins = 0
    refresh_refused = False

    def __init__(self, session, cfg, on_auth_data_update=None):
        self.session = session
        self.expiring = False

    async def login(self):
        Client.logins += 1
        await asyncio.sleep(0.01)
        if Client.fail:
            raise Client.fail

    def _is_token_expiring(self):
        return self.expiring

    async def _ensure_token(self):
        if self.expiring:
            if Client.refresh_refused:
                raise CookidooAuthException("refresh token rejected")
            self.expiring = False

    async def call(self):
        await asyncio.sleep(0.01)
        if self.session.closed:
            raise RuntimeError("Session is closed")
        return "ok"


@pytest.fixture
def svc(monkeypatch):
    Session.created, Client.logins, Client.fail, Client.refresh_refused = [], 0, None, False
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


async def test_relogin_swaps_the_session_after_success(svc, monkeypatch):
    monkeypatch.setattr(cookidoo_service, "RETIRE_DELAY", 0.01)
    first = await svc.client()
    second = await svc.login()
    assert second is not first and await svc.client() is second
    old, new = Session.created
    assert not old.closed  # calls still running on it may finish
    await asyncio.sleep(0.05)
    assert old.closed and not new.closed


async def test_relogin_does_not_break_calls_in_flight(svc):
    first = await svc.client()
    in_flight = asyncio.create_task(first.call())
    await asyncio.sleep(0)
    await svc.login()  # connect_to_cookidoo while another tool call is running
    assert await in_flight == "ok"
    await svc.close()
    assert all(session.closed for session in Session.created)


async def test_expired_refresh_token_leads_to_a_new_login(svc):
    first = await svc.client()
    first.expiring, Client.refresh_refused = True, True
    second = await svc.client()
    assert second is not first and Client.logins == 2


async def test_expiring_token_is_refreshed_without_login(svc):
    first = await svc.client()
    first.expiring = True
    assert await svc.client() is first and Client.logins == 1


async def test_refused_credentials_are_not_retried_on_every_call(svc):
    Client.fail = CookidooAuthException("wrong password")
    for _ in range(3):
        with pytest.raises(CookidooAuthException, match="wrong password"):
            await svc.client()
    assert Client.logins == 1
    with pytest.raises(CookidooAuthException):
        await svc.login()  # an explicit connect_to_cookidoo does try again
    assert Client.logins == 2


async def test_failed_relogin_keeps_the_working_session(svc):
    first = await svc.client()
    Client.fail = RuntimeError("login refused")
    with pytest.raises(RuntimeError, match="login refused"):
        await svc.login()
    assert await svc.client() is first and await first.call() == "ok"
    old, failed = Session.created
    assert not old.closed and failed.closed


async def test_failed_first_login_leaves_no_session(svc):
    Client.fail = RuntimeError("login refused")
    with pytest.raises(RuntimeError):
        await svc.client()
    assert svc._api_client is None and svc._session is None
    assert Session.created[0].closed


async def test_close(svc):
    await svc.client()
    await svc.close()
    assert Session.created[0].closed and svc._api_client is None
