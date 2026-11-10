# Base images pinned by digest; Dependabot keeps them up to date.

# --- Build stage: resolve the locked dependencies into a virtualenv -----------------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58 AS build

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_CACHE=1 UV_PYTHON_DOWNLOADS=never

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

# --- Runtime stage: plain Python, no uv, no build tools ----------------------------------
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

# Pick up Debian security fixes released after the base image was built
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=build /app/.venv /app/.venv
# Only the server modules and the prompt templates; no tests, no .env
COPY pyproject.toml server.py cookidoo_service.py guided.py schemas.py security.py prompt.md prompt_FR.md ./

RUN useradd --system --uid 10001 --no-create-home app
USER app

# Credentials come from the runtime environment (docker run -e / --env-file), never baked in.
ENTRYPOINT ["/app/.venv/bin/python", "server.py"]
