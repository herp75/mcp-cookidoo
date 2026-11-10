# Base images pinned by digest; Dependabot keeps them up to date.
# Alpine (musl): every compiled dependency in uv.lock ships musllinux wheels for amd64/arm64.

# --- Build stage: resolve the locked dependencies into a virtualenv -----------------------
FROM ghcr.io/astral-sh/uv:python3.12-alpine@sha256:aef1a1327a732e74892765e22fa528499eca3f828140a66ed46b2f8df51bd0dc AS build

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_CACHE=1 UV_PYTHON_DOWNLOADS=never

COPY pyproject.toml uv.lock ./
# Only http-ece (pure Python, sdist only) is built from source. The image has no C compiler,
# so a compiled dependency without a musl wheel makes the build fail instead of slipping in.
RUN uv sync --locked --no-dev

# --- Runtime stage: plain Python, no uv, no build tools ----------------------------------
FROM python:3.12-alpine@sha256:e064671ca452259bab50e8e2c7bd896e39e9a343bb06ca25234adf6fe18d0fc7

# Pick up Alpine security fixes released after the base image was built
RUN apk upgrade --no-cache

WORKDIR /app
COPY --from=build /app/.venv /app/.venv
# Only the server modules and the prompt templates; no tests, no .env
COPY pyproject.toml server.py cookidoo_service.py guided.py schemas.py security.py recipe_images.py devices.py prompt.md prompt_FR.md prompt_DE.md ./
COPY tools/*.py ./tools/

RUN adduser -S -D -H -u 10001 app
USER app

# Credentials come from the runtime environment (docker run -e / --env-file), never baked in.
ENTRYPOINT ["/app/.venv/bin/python", "server.py"]
