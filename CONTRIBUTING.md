# Contributing

Thanks for helping. Please read [SECURITY.md](SECURITY.md) first: this server holds people's
Cookidoo passwords, and some rules below exist because of that.

**Never paste credentials, tokens, cookies or unredacted logs into an issue, pull request or
discussion.** Report vulnerabilities privately (Security tab → Report a vulnerability).

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env          # only to run the server against your account
```

## Checks

All of them run in CI and must pass:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest --cov           # coverage must stay at 85 % or more
scripts/check_insecure_patterns.sh
```

The unit tests never contact Cookidoo: they run the tools through MCP against an in-memory
fake (`tests/conftest.py`), with sanitized fixtures in `tests/fixtures/`. Fixtures must not
contain real names, e-mail addresses or IDs.

## Live tests

`tests/live` runs against the real Cookidoo API and is skipped unless asked for:

- Only with a **dedicated test account**, never a personal one. Credentials go in
  `.env.live` (git-ignored, see `.env.live.example`), never on the command line.
- `--live` runs read-only tests; `--live-write` also creates and deletes data. A test that
  writes must name what it creates with `name(...)` (prefix "MCP live test") and remove it in
  `finally`.
- Run them with `-p no:logging --assert=plain`, so failures do not print account data.
- In CI they run weekly from `main` only, with the secrets of the `live` environment.

## Changes

- Keep the layout and conventions described in [docs/architecture.md](docs/architecture.md),
  including the checklist for adding a tool (annotations, confirm, snapshot, tests).
- Line length 100, ruff rules `E,F,I,UP,B,ASYNC`.
- Never add: disabled TLS verification, default binds to all interfaces, optional HTTP
  tokens, unbounded dependency ranges, or file access outside `COOKIDOO_IMAGE_DIRS`. CI
  guards some of these; reviews guard the rest.
- Dependencies: a lower and an upper bound (`>=x.y,<x+1`), lock with `uv lock`. A new major
  version needs a note in the PR on what changed.
- Code from other projects: only under a compatible license, credited in a file header and
  in [NOTICE](NOTICE). Nothing from GPL-licensed projects.
- Update `CHANGELOG.md` (`## [Unreleased]`, Keep a Changelog sections) and the README tool
  table in the same PR.

## Commits and pull requests

- One topic per pull request, on a branch; `main` is protected and needs a review.
- Commit subjects in the imperative, under ~70 characters, with a body explaining why when it
  is not obvious.
- The PR description says what changed, how it was tested, and anything a reviewer should
  look at closely (security-relevant changes in particular).
