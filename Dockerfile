# Base image pinned by digest; Dependabot keeps it up to date.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_CACHE=1

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

# Only the server modules and the prompt templates; no tests, no .env
COPY server.py cookidoo_service.py guided.py schemas.py security.py prompt.md prompt_FR.md ./

RUN useradd --system --uid 10001 --no-create-home app
USER app

# Credentials come from the runtime environment (docker run -e / --env-file), never baked in.
ENTRYPOINT ["/app/.venv/bin/python", "server.py"]
