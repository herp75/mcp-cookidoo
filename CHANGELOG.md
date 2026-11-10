# Changelog

## [Unreleased]

### Added
- `copy_recipe_to_custom`: copy a catalogue recipe into a new custom recipe (keeping its
  serving size unless another is given) and return the new ID for editing
- `move_in_meal_plan`: move a planned recipe to another day; it is added to the new day
  before being removed from the old one, and rolled back if that fails; catalogue or custom
  recipe is detected from the plan
- `upload_custom_recipe_image`: set a custom recipe's photo from a local file, ported from
  vitaliemiron/cookidoo-mcp (Apache-2.0, see NOTICE) and hardened: disabled unless
  `COOKIDOO_IMAGE_DIRS` is set, path allowlist checked after resolving symlinks, JPEG/PNG/WebP
  only (checked by content), 10 MB and 50 MP limits, always re-encoded to JPEG without
  EXIF/GPS metadata, TLS verification on, no Cookidoo credentials sent to Cloudinary
- Pillow 13 dependency
- Guided cooking steps in German: settings are written in the notation of the cookidoo.de
  recipe editor (`3 Min./120°C/Linkslauf/Stufe 1`, `Teig kneten/2 Min.`, `Turbo/1 Sek./3x`)
  when `COOKIDOO_LANGUAGE` is German, and a German `create_thermomix_recipe` prompt
  (`language="de"`)
- Shopping list tools: `get_shopping_list`, `add_recipes_to_shopping_list`,
  `remove_recipes_from_shopping_list`, `add_shopping_list_items`, `edit_shopping_list_items`,
  `mark_shopping_list_items`, `remove_shopping_list_items`, `clear_shopping_list`
- Meal plan tools: `get_meal_plan_week`, `add_to_meal_plan`, `remove_from_meal_plan`
- Collection tools: `list_collections` (paginated), `follow_collection`, `unfollow_collection`,
  `create_collection`, `delete_collection`, `add_to_collection`, `remove_from_collection`
- Account tools: `get_account_info`, `list_devices`; and `delete_custom_recipe`
- Tools that accept recipes take official (`recipe_ids`) and custom (`custom_recipe_ids`) IDs
- `clear_shopping_list`, `delete_collection` and `delete_custom_recipe` refuse to run without
  `confirm=true`; destructive tools say in their description what is lost
- Names coming from Cookidoo are collapsed onto one line in tool output, so they cannot fake
  extra fields
- Tool-contract snapshot test (`tests/snapshots/tools.json`): names, annotations and parameters
  of every tool; destructive/read-only hints and `confirm` checked by naming rules
- HTTP mode via `server.py --transport http`: binds to `127.0.0.1` by default; a non-loopback
  `COOKIDOO_MCP_HOST` requires `COOKIDOO_MCP_TOKEN` (32+ characters), checked as a bearer
  token in constant time on every request; unauthenticated `GET /health`
- Secrets (password, tokens, `Authorization` headers, cookies) are redacted from all log
  records and from tool error messages
- Recipe IDs are validated before being put into a URL; `search_recipes` caps `max_results` at 50
- Server instructions tell the model to treat recipe content as data, not instructions
- `LICENSE` (MIT), `NOTICE`, `SECURITY.md`
- CI: Python 3.12 / 3.13 matrix, pyright, coverage gate (85 %); security workflow
  (pip-audit, gitleaks, insecure-pattern guard, dependency review); CodeQL; Docker image built
  and scanned with Trivy on PRs, pushed with provenance and SBOM on tags; Dependabot.
  All actions pinned by commit SHA with least-privilege permissions

### Fixed
- `get_meal_plan_week` described and labelled the result as the calendar week containing
  `day`; Cookidoo returns the 7 days starting at `day`, which is now what it says

### Changed
- New tools live in a `tools/` package, one module per group
- Docker image: Alpine-based multi-stage build (uv only in the build stage), runs as a
  non-root user, base images pinned by digest and security updates applied, only server
  files copied

### Security
- fastmcp upgraded to 4.1.2 (GHSA-7xgj-8392-5q4r, GHSA-vqqp-x5qm-5qp4)

## [1.1.0] - 2026-10-20

### Added
- Guided cooking: a recipe step can be an object whose Thermomix settings are written into its
  text and preset on the machine: manual settings (time, temperature, speed, reverse) and the
  dough, browning, steaming, turbo, rice cooker, warm-up and blend modes, with the limits the
  Cookidoo API accepts. Plain-text steps are unchanged
- Ingredient links: words of a step can be linked to a line of the ingredient list
- `get_custom_recipe` returns steps with settings and links in the same format (read from the
  raw API response, so turbo pulses and sub-second times are kept), so editing a recipe keeps
  them
- The `create_thermomix_recipe` prompt asks for structured steps

## [1.0.0] - 2026-10-20

First stable release, rebuilt on cookidoo-api 0.19 and FastMCP 4.

### Added
- `search_recipes`: search the catalogue by keywords and/or ingredients, with a maximum total time
- `list_custom_recipes`, `get_custom_recipe` and `update_custom_recipe` to read and edit your own recipes
- `create_thermomix_recipe` MCP prompt (English / French) for the guided recipe workflow
- Automatic login on first use; `connect_to_cookidoo` is now optional
- `COOKIDOO_COUNTRY`, `COOKIDOO_LANGUAGE` and `COOKIDOO_DEVICE` settings
- STDIO entrypoint (`uv run server.py`) for Claude Desktop / Claude Code
- Docker image published to `ghcr.io/alexandrepa/mcp-cookidoo`
- Tests and CI

### Changed
- Recipe creation uses cookidoo-api's native custom-recipe API instead of hand-written HTTP calls
- `generate_recipe_structure` accepts lists, and only splits text on new lines, so ingredients like "300 g potatoes, sliced" stay intact
- Tool errors are reported as MCP errors, and tools carry read-only / write annotations
- Requires Python 3.12+; dependencies managed with uv (`pyproject.toml`, `uv.lock`)

### Fixed
- Recipe upload failing with recent cookidoo-api versions (`'Cookidoo' object has no attribute 'auth_data'`)
- TLS certificate verification was disabled
- Debug output on stdout broke the STDIO transport
- Blocking `time.sleep` in async code
- Recipe URLs starting with `https://https://`
- `get_recipe_details` never showed the steps and showed seconds as minutes

## [0.1.0] - 2025-10-12

Initial version: connect, get recipe details, generate and upload custom recipes.

[1.0.0]: https://github.com/alexandrepa/mcp-cookidoo/releases/tag/v1.0.0
