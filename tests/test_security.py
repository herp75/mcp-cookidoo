import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from cookidoo_api import CookidooRequestException
from fastmcp import Client
from fastmcp.exceptions import ToolError

import security
import server
from cookidoo_service import CookidooService, CookidooSettings
from security import (
    BearerTokenMiddleware,
    check_http_config,
    install_log_redaction,
    is_loopback,
    redact,
    register_secret,
    validate_id,
    validate_ids,
)

TOKEN = "t" * 16 + "0123456789abcdef"  # 32 characters
PASSWORD = "correct-horse-battery-staple"
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}
MCP_HEADERS = {"accept": "application/json, text/event-stream"}


@pytest.fixture
def secrets(monkeypatch):
    monkeypatch.setattr(security, "_secrets", set())
    register_secret(PASSWORD)
    register_secret(TOKEN)


@pytest.fixture
def service(monkeypatch, secrets):
    svc = CookidooService(CookidooSettings(email="a@b.c", password=PASSWORD))
    for name in ("login", "get_recipe_details", "get_custom_recipe", "update_custom_recipe"):
        monkeypatch.setattr(svc, name, AsyncMock())
    monkeypatch.setattr(server, "_cookidoo_service", svc)
    return svc


# --- HTTP transport -----------------------------------------------------------------------


@asynccontextmanager
async def http_client(token: str | None = TOKEN):
    # Entered inside each test (not an async fixture): the MCP session manager's task group
    # must be entered and exited in the same task.
    app = server.mcp.http_app(middleware=server.http_middleware(token))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
            yield c


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"authorization": "Bearer wrong-token"},
        {"authorization": f"Basic {TOKEN}"},
        {"authorization": f"Bearer {TOKEN}x"},
        {"authorization": TOKEN},
    ],
)
async def test_http_rejects_missing_or_wrong_token(headers):
    async with http_client() as client:
        response = await client.post("/mcp", json=INITIALIZE, headers={**MCP_HEADERS, **headers})
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}
    assert response.headers["www-authenticate"].startswith("Bearer")


async def test_http_accepts_correct_token():
    async with http_client() as client:
        response = await client.post(
            "/mcp", json=INITIALIZE, headers={**MCP_HEADERS, "authorization": f"Bearer {TOKEN}"}
        )
    assert response.status_code == 200
    assert "cookidoo-mcp-server" in response.text


async def test_health_needs_no_token_and_reveals_nothing():
    async with http_client() as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_http_without_token_is_open_on_loopback():
    async with http_client(token=None) as client:
        response = await client.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS)
    assert response.status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {"host": "evil.example"},  # DNS rebinding: the attacker's name resolving to 127.0.0.1
        {"host": "127.0.0.1.evil.example:8000"},
        {"origin": "https://evil.example"},
        {"origin": "null"},
    ],
)
async def test_http_without_token_refuses_requests_from_web_pages(headers):
    async with http_client(token=None) as client:
        response = await client.post("/mcp", json=INITIALIZE, headers={**MCP_HEADERS, **headers})
    assert response.status_code == 403


@pytest.mark.parametrize(
    "headers",
    [
        {"host": "localhost:8000"},
        {"host": "[::1]:8000", "origin": "http://[::1]:8000"},
        {"origin": "http://localhost:6274"},  # e.g. the MCP Inspector
    ],
)
async def test_http_without_token_accepts_local_requests(headers):
    async with http_client(token=None) as client:
        response = await client.post("/mcp", json=INITIALIZE, headers={**MCP_HEADERS, **headers})
    assert response.status_code == 200


async def test_http_with_token_accepts_any_host():
    async with http_client() as client:
        headers = {"host": "mcp.example", "authorization": f"Bearer {TOKEN}"}
        response = await client.post("/mcp", json=INITIALIZE, headers={**MCP_HEADERS, **headers})
    assert response.status_code == 200


async def test_websocket_connections_are_refused():
    app, sent = AsyncMock(), []

    async def send(message):
        sent.append(message)

    await BearerTokenMiddleware(app, token=None)({"type": "websocket"}, AsyncMock(), send)
    assert sent == [{"type": "websocket.close", "code": 1008}]
    app.assert_not_awaited()


def test_encoded_forms_of_the_password_are_masked(monkeypatch):
    monkeypatch.setattr(security, "_secrets", set())
    register_secret('p@ss w"rd&é')
    for form in (
        "p%40ss+w%22rd%26%C3%A9",  # form body
        "p%40ss%20w%22rd%26%C3%A9",  # URL
        'p@ss w\\"rd&\\u00e9',  # JSON
        'p@ss w\\"rd&é',  # JSON, not ASCII-escaped
    ):
        assert redact(f"password sent as {form}.") == "password sent as ***."


async def test_middleware_compares_in_constant_time(monkeypatch):
    compare = MagicMock(return_value=True)
    monkeypatch.setattr(security.hmac, "compare_digest", compare)
    inner = AsyncMock()
    scope = {"type": "http", "path": "/mcp", "method": "POST", "headers": []}
    scope["headers"] = [(b"authorization", f"Bearer {TOKEN}".encode())]
    await BearerTokenMiddleware(inner, TOKEN)(scope, AsyncMock(), AsyncMock())
    compare.assert_called_once_with(TOKEN.encode(), TOKEN.encode())
    inner.assert_awaited_once()


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "[::1]", "127.0.0.2"])
def test_loopback_hosts(host):
    assert is_loopback(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10", "example.com", ""])
def test_non_loopback_hosts(host):
    assert not is_loopback(host)


def test_http_config_checks():
    check_http_config("127.0.0.1", None)
    check_http_config("0.0.0.0", TOKEN)
    with pytest.raises(ValueError, match="non-loopback"):
        check_http_config("0.0.0.0", None)
    with pytest.raises(ValueError, match="at least 32"):
        check_http_config("127.0.0.1", "short")


@pytest.fixture
def run(monkeypatch):
    for var in ("COOKIDOO_MCP_HOST", "COOKIDOO_MCP_TOKEN", "COOKIDOO_MCP_TRANSPORT"):
        monkeypatch.delenv(var, raising=False)
    mock = MagicMock()
    monkeypatch.setattr(server.mcp, "run", mock)
    return mock


def test_main_defaults_to_stdio(run):
    server.main([])
    run.assert_called_once_with()


def test_main_http_binds_loopback_by_default(run):
    server.main(["--transport", "http"])
    assert run.call_args.kwargs["host"] == "127.0.0.1"


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "myhost.lan"])
def test_main_refuses_public_host_without_token(run, host, capsys):
    with pytest.raises(SystemExit) as exit_info:
        server.main(["--transport", "http", "--host", host])
    assert exit_info.value.code == 2
    assert "COOKIDOO_MCP_TOKEN" in capsys.readouterr().err
    run.assert_not_called()


def test_main_refuses_short_token(run, monkeypatch):
    monkeypatch.setenv("COOKIDOO_MCP_TOKEN", "too-short")
    with pytest.raises(SystemExit):
        server.main(["--transport", "http"])
    run.assert_not_called()


def test_main_public_host_with_token(run, monkeypatch, secrets):
    monkeypatch.setenv("COOKIDOO_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("COOKIDOO_MCP_TOKEN", TOKEN)
    server.main(["--transport", "http"])
    kwargs = run.call_args.kwargs
    assert kwargs["host"] == "0.0.0.0" and kwargs["transport"] == "http"
    assert kwargs["middleware"][0].kwargs == {"token": TOKEN}


# --- Redaction -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        f"login failed for password {PASSWORD}",
        "headers={'Authorization': 'Bearer eyJhbGciOi.abc.def'}",
        "Authorization: Basic dXNlcjpwYXNz",
        "Cookie: _oauth2_proxy=abcdef123; v-token=xyz",
        "Set-Cookie: session=0123456789; Path=/",
        '{"access_token": "at-123456", "refresh_token": "rt-654321"}',
        "POST /login password=hunter22&username=me",
        f"Bearer {TOKEN}",
    ],
)
def test_redact(secrets, text):
    redacted = redact(text)
    for secret in (
        PASSWORD,
        TOKEN,
        "eyJhbGciOi",
        "dXNlcjpwYXNz",
        "abcdef123",
        "xyz",
        "0123456789",
        "at-123456",
        "rt-654321",
        "hunter22",
    ):
        assert secret not in redacted
    assert "***" in redacted


def test_redact_keeps_normal_text(secrets):
    assert redact("Recipe 'Cookies' not found (404)") == "Recipe 'Cookies' not found (404)"


def test_log_records_are_redacted(secrets, caplog):
    install_log_redaction()
    logger = logging.getLogger("aiohttp.client")  # a child logger, not the root
    with caplog.at_level(logging.DEBUG):
        logger.debug("sending %s", {"Authorization": f"Bearer {TOKEN}"})
        logger.info("password=%s", PASSWORD)
        logger.warning("Cookie: auth=abc123def")
        try:
            raise RuntimeError(f"failed with {PASSWORD}")
        except RuntimeError:
            logger.exception("boom")
    text = caplog.text
    assert TOKEN not in text and PASSWORD not in text and "abc123def" not in text
    assert text.count("***") >= 4


def test_redacting_filter(secrets):
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "pw %s", (PASSWORD,), None)
    assert security.RedactingFilter().filter(record)
    assert record.getMessage() == "pw ***"


async def test_tool_errors_contain_no_secrets(service):
    service.get_recipe_details.side_effect = CookidooRequestException(
        f"Error 500 for https://cookidoo.fr headers={{'Authorization': 'Bearer {TOKEN}'}} "
        f"body=password={PASSWORD}"
    )
    async with Client(server.mcp) as client:
        with pytest.raises(ToolError) as error:
            await client.call_tool("get_recipe_details", {"recipe_id": "r1"})
    message = str(error.value)
    assert message.startswith("Failed to get recipe details")
    assert TOKEN not in message and PASSWORD not in message


async def test_connection_error_contains_no_password(service):
    service.login.side_effect = RuntimeError(f"bad credentials: {PASSWORD}")
    async with Client(server.mcp) as client:
        with pytest.raises(ToolError) as error:
            await client.call_tool("connect_to_cookidoo", {})
    assert PASSWORD not in str(error.value)


def test_password_from_settings_is_registered(monkeypatch):
    monkeypatch.setattr(security, "_secrets", set())
    monkeypatch.setattr("cookidoo_service.load_dotenv", lambda: None)
    monkeypatch.setenv("COOKIDOO_EMAIL", "a@b.c")
    monkeypatch.setenv("COOKIDOO_PASSWORD", "my-secret-pw")
    from cookidoo_service import load_cookidoo_settings

    load_cookidoo_settings()
    assert redact("x my-secret-pw y") == "x *** y"


# --- Input validation ----------------------------------------------------------------------


@pytest.mark.parametrize("value", ["r59322", "01JABCDEF_xyz-9", "a" * 64, " r1 "])
def test_valid_ids(value):
    assert validate_id(value) == value.strip()


@pytest.mark.parametrize(
    "value",
    ["", "../etc/passwd", "r1/../../x", "https://evil.example/x", "a" * 65, "r1?x=1", "r 1"],
)
def test_invalid_ids(value):
    with pytest.raises(ToolError, match="Invalid recipe ID"):
        validate_id(value)


def test_validate_ids_caps_and_dedupes():
    assert validate_ids(["r1", "r2", "r1"]) == ["r1", "r2"]
    with pytest.raises(ToolError, match="at most 50"):
        validate_ids([f"r{i}" for i in range(51)])


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("get_recipe_details", {"recipe_id": "../x"}),
        ("get_custom_recipe", {"recipe_id": "https://evil.example"}),
        ("update_custom_recipe", {"recipe_id": "a/b", "recipe_json": "{}"}),
    ],
)
async def test_tools_reject_bad_ids_before_calling_cookidoo(service, tool, args):
    async with Client(server.mcp) as client:
        with pytest.raises(ToolError, match="Invalid recipe ID"):
            await client.call_tool(tool, args)
    service.get_recipe_details.assert_not_called()
    service.get_custom_recipe.assert_not_called()
    service.update_custom_recipe.assert_not_called()


async def test_search_caps_max_results(service):
    async with Client(server.mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("search_recipes", {"query": "soup", "max_results": 500})


def test_empty_optional_settings_use_the_defaults(monkeypatch):
    """Claude Desktop passes optional bundle fields left blank as empty variables."""
    monkeypatch.setattr("cookidoo_service.load_dotenv", lambda: None)
    monkeypatch.setenv("COOKIDOO_EMAIL", "a@b.c")
    monkeypatch.setenv("COOKIDOO_PASSWORD", "my-secret-pw")
    for name in ("COOKIDOO_COUNTRY", "COOKIDOO_LANGUAGE", "COOKIDOO_DEVICE"):
        monkeypatch.setenv(name, "")
    from cookidoo_service import load_cookidoo_settings

    settings = load_cookidoo_settings()
    assert (settings.country, settings.language, settings.device) == ("fr", "fr-FR", None)
