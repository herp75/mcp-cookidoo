"""
Security helpers: secret redaction, input validation and HTTP bearer-token auth.

This server holds the user's Cookidoo password. Nothing in here may be relaxed without a
security review: see SECURITY.md.
"""

import hmac
import ipaddress
import json
import logging
import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import quote, quote_plus, urlsplit

from fastmcp.exceptions import ToolError

MASK = "***"
MIN_TOKEN_LENGTH = 32
MAX_IDS = 50
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Secret values known at runtime (password, HTTP token). Masked wherever they appear.
_secrets: set[str] = set()

# Secrets that can be recognised by their shape, whatever their value.
_PATTERNS = [
    # Authorization / Cookie / Set-Cookie headers, in "Name: value" or "'Name': 'value'" form
    re.compile(
        r"(?i)(['\"]?(?:authorization|proxy-authorization|cookie|set-cookie)['\"]?\s*[:=]\s*['\"]?)"
        r"[^'\"\n]+"
    ),
    # Bearer tokens anywhere
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
    # key=value or "key": "value" for token / password fields
    re.compile(
        r"(?i)(['\"]?(?:access_token|refresh_token|id_token|token|password|passwd|secret|"
        r"client_secret|api_key|session)['\"]?\s*[:=]\s*['\"]?)[^'\"&\s,}]+"
    ),
]


def register_secret(value: str | None) -> None:
    """Remember a secret value so that redact() masks it."""
    if value and len(value) >= 4:
        # Also as it appears in a form or URL, and inside a JSON string
        _secrets.update(
            {
                value,
                quote(value, safe=""),
                quote_plus(value),
                json.dumps(value)[1:-1],
                json.dumps(value, ensure_ascii=False)[1:-1],
            }
        )


def redact(text: object) -> str:
    """Mask passwords, tokens, Authorization headers and cookie values in a text."""
    result = str(text)
    for secret in sorted(_secrets, key=len, reverse=True):
        result = result.replace(secret, MASK)
    for pattern in _PATTERNS:
        result = pattern.sub(lambda m: m.group(1) + MASK, result)
    return result


def tool_error(message: str, error: BaseException) -> ToolError:
    """A ToolError for a failed Cookidoo call, with secrets removed from the message."""
    return ToolError(redact(f"{message}: {error}"))


class RedactingFilter(logging.Filter):
    """Logging filter that redacts the formatted message and any traceback text."""

    def filter(self, record: logging.LogRecord) -> bool:
        _redact_record(record)
        return True


def _redact_record(record: logging.LogRecord) -> None:
    if getattr(record, "_redacted", False):
        return
    try:
        message = record.getMessage()
    except Exception:  # malformed format args: keep the raw message
        message = str(record.msg)
    record.msg = redact(message)
    record.args = ()
    if record.exc_info and not record.exc_text:
        record.exc_text = logging.Formatter().formatException(record.exc_info)
    if record.exc_text:
        record.exc_text = redact(record.exc_text)
    record._redacted = True  # type: ignore[attr-defined]


_installed = False


def install_log_redaction() -> None:
    """
    Redact every log record created in this process, whatever logger or handler it goes to.

    A record factory is used rather than a filter on one logger, because filters do not
    apply to records propagated from child loggers (aiohttp, fastmcp, ...).
    """
    global _installed
    if _installed:
        return
    previous = logging.getLogRecordFactory()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        _redact_record(record)
        return record

    logging.setLogRecordFactory(factory)
    _installed = True


def validate_id(value: str, what: str = "recipe ID") -> str:
    """Reject anything but a plain Cookidoo ID before it is put into a URL."""
    value = value.strip()
    if not ID_PATTERN.fullmatch(value):
        raise ToolError(
            f"Invalid {what}: expected 1-64 letters, digits, '-' or '_' (e.g. 'r59322')."
        )
    return value


def validate_ids(values: Iterable[str], what: str = "recipe ID") -> list[str]:
    """Validate a list of IDs, at most MAX_IDS, keeping order and dropping duplicates."""
    ids = list(dict.fromkeys(validate_id(v, what) for v in values))
    if len(ids) > MAX_IDS:
        raise ToolError(f"Too many {what}s: at most {MAX_IDS} per call.")
    return ids


# --- HTTP transport ---------------------------------------------------------------------


def is_loopback(host: str) -> bool:
    """True if binding to this host only accepts connections from this machine."""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False  # host names other than localhost may resolve to anything


def check_http_config(host: str, token: str | None) -> None:
    """
    Refuse unsafe HTTP configurations.

    Raises:
        ValueError: if the token is too short, or the host is not loopback and no token is set
    """
    if token is not None and len(token) < MIN_TOKEN_LENGTH:
        raise ValueError(f"COOKIDOO_MCP_TOKEN must be at least {MIN_TOKEN_LENGTH} characters long")
    if not is_loopback(host) and not token:
        raise ValueError(
            f"Refusing to listen on non-loopback host '{host}' without COOKIDOO_MCP_TOKEN. "
            "Set a token of at least 32 random characters, or bind to 127.0.0.1."
        )


_LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}


def _header(scope: dict, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return value.decode("latin-1")
    return None


def _is_local_host(host_header: str | None) -> bool:
    """A Host header naming this machine: localhost, 127.0.0.1 or [::1], any port."""
    if not host_header:
        return False
    host = urlsplit(f"//{host_header}").hostname
    return host in _LOOPBACK_NAMES


def _is_local_origin(origin: str | None) -> bool:
    """No Origin (not a browser) or a page served from this machine."""
    if origin is None:
        return True
    parts = urlsplit(origin)
    return parts.scheme in ("http", "https") and parts.hostname in _LOOPBACK_NAMES


class BearerTokenMiddleware:
    """
    ASGI middleware: answers GET /health, and requires `Authorization: Bearer <token>`
    on every other request when a token is configured. Tokens are compared in constant time.

    Without a token (only allowed on loopback), requests must name this machine in Host and,
    from a browser, come from a page on this machine (Origin). That stops web pages from
    reaching the server through DNS rebinding; FastMCP itself does not check either header.
    WebSocket connections are refused: the MCP transports do not use them.
    """

    def __init__(self, app: Any, token: str | None, health_path: str = "/health"):
        self.app = app
        self._token = token.encode() if token else None
        self._health_path = health_path

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":  # lifespan
            await self.app(scope, receive, send)
            return
        if scope.get("path") == self._health_path and scope.get("method") in ("GET", "HEAD"):
            await _send_json(send, 200, {"status": "ok"})
            return
        if self._token is None and not (
            _is_local_host(_header(scope, b"host")) and _is_local_origin(_header(scope, b"origin"))
        ):
            await _send_json(send, 403, {"error": "forbidden: not a local request"})
            return
        if self._token is not None and not self._authorized(scope):
            await _send_json(
                send,
                401,
                {"error": "unauthorized"},
                [(b"www-authenticate", b'Bearer realm="cookidoo-mcp"')],
            )
            return
        await self.app(scope, receive, send)

    def _authorized(self, scope: dict) -> bool:
        assert self._token is not None
        for name, value in scope.get("headers", []):
            if name.lower() == b"authorization":
                scheme, _, presented = value.partition(b" ")
                if scheme.lower() == b"bearer":
                    return hmac.compare_digest(presented.strip(), self._token)
                return False
        return False


async def _send_json(
    send: Any, status: int, body: dict, extra_headers: list[tuple[bytes, bytes]] | None = None
) -> None:
    payload = json.dumps(body).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(payload)).encode()),
        *(extra_headers or []),
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": payload})
