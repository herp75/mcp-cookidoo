# Changelog

## [Unreleased]

### Added
- Weekly "Upstream changes" workflow: opens (or comments on) an issue when
  alexandrepa/mcp-cookidoo has commits that are not in `main`, each upstream head reported once

## [2.0.0] - 2026-11-10

Major release of the fork: 25 new tools, device-aware recipe validation, a security baseline
and release automation. No existing tool was removed or renamed and no parameter changed.

### Breaking
- Recipe settings are checked against the Cookidoo editor's limits for the target Thermomix
  (see Added). Some recipe JSON accepted by 1.1.0 is refused, e.g. blend at speed 10, dough
  over 20 min, browning or steaming for a TM6, Varoma on a TM7. Recipes already on Cookidoo
  stay readable and are only checked when saved again
- `COOKIDOO_DEVICE` no longer accepts TM31 and no longer defaults to TM6 (see Changed)

### Added
- Release workflow on `v*` tags: full CI and security checks, then the Docker image (signed
  keyless with cosign), a Claude Desktop bundle (`.mcpb`, uv runtime) with build provenance,
  a GitHub Release with this changelog section, and the MCP Registry entry
  `io.github.herp75/mcp-cookidoo` (`server.json`). No PyPI package
- `edge` and `sha-*` Docker images are signed too
- `edge` Docker image (amd64/arm64, provenance and SBOM) pushed to GHCR on every change on
  `main`, besides the version tags of releases
- The scheduled live run opens (or comments on) an issue "Live tests failed"
- `docs/architecture.md` (modules, service/tool split, session, step and device flow, how to
  add a tool) and `CONTRIBUTING.md`; README credits, a fuller disclaimer, Docker with an env
  file and in HTTP mode
- Live tests (`tests/live`) against the real Cookidoo API with a dedicated test account:
  read-only with `--live`, plus create/update/delete round trips with `--live-write` (TM6 and
  TM7 recipes, copy, image upload, collections, shopping list, meal plan), each removing what
  it created. Credentials only from `COOKIDOO_TEST_*` / `.env.live`; every tool output and log
  record of the run is checked for the password and tokens
- Weekly "Live tests" workflow (and on demand), with the secrets in a `live` environment; it
  compares the Cookidoo recipe editor (page and script) with the limits in `devices.py` and
  opens an issue when they differ
- Thermomix device awareness: `generate_recipe_structure`, `upload_custom_recipe` and
  `update_custom_recipe` take an optional `device` (`TM5`, `TM6`, `TM7`) and refuse settings
  the device does not support, with the limits of the Cookidoo recipe editor (browning and
  steaming TM7 only, Varoma temperature TM6 only, turbo pulses TM7 only, blend 10 s – 5 min at
  speed 6–8, warm up 37–90 °C at soft/1/2, dough ≤ 20 min, manual ≤ 99 min and ≤ speed 6 with
  heat; TM5 manual settings only). Errors name the allowed values; off-grid times warn
- Default device: the recipe's own devices (update), `COOKIDOO_DEVICE`, the linked devices
  (TM6 rules if a TM6 is among them), then TM6
- Live mode catalog read from the account's Cookidoo recipe editor page (cached 1 h, built-in
  snapshot as fallback, untrusted input: only known names and values, it can only restrict)
- `get_thermomix_modes(device)`: settings and modes per device with limits and examples
- Prompts (en/fr/de) ask for the target Thermomix and pass it on
- Mode rules, editor parser and editor fixtures from tobilektri/mcp-cookidoo (MIT, see NOTICE);
  analysis in `docs/device-modes-analysis.md`
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
- Several tool calls at once could fail with "Session is closed": each started its own first
  login and closed the session the others were using. They now share one login; a forced
  re-login (`connect_to_cookidoo`) swaps the session only after it succeeded
- Empty optional settings (as passed by Claude Desktop for blank fields) use the defaults
- The Docker workflow did not rebuild the image for changes in `tools/` or the prompts
- The mode catalog read only the first chunk of the editor page, so it fell back to the
  built-in list on real connections (found by the live tests)
- `generate_recipe_structure` accepted steps the upload then refused (a setting written twice
  in one step, e.g. "Reis kochen {settings}"); it now renders the steps as the upload does
- Images under 80 pixels on a side are refused before upload (Cloudinary's moderation rejects
  them), and Cloudinary's reason is shown, cleaned up, when it rejects an upload
- Turbo sends `pulseCount` only for more than one pulse (a TM7 setting), as the Cookidoo
  editor does, and the step text no longer says "1x"
- `get_meal_plan_week` described and labelled the result as the calendar week containing
  `day`; Cookidoo returns the 7 days starting at `day`, which is now what it says

### Changed
- Tool descriptions state their side effects more precisely: `generate_recipe_structure`
  changes nothing, `upload_custom_recipe` / `copy_recipe_to_custom` / `create_collection`
  create another item on each call, `update_custom_recipe` overwrites for good
- `COOKIDOO_DEVICE` no longer defaults to TM6 (unset: detected from the account) and no longer
  accepts TM31
- `generate_recipe_structure` returns the recipe JSON as the first content block, unchanged,
  and the device check notes as a second block
- New tools live in a `tools/` package, one module per group
- Docker image: Alpine-based multi-stage build (uv only in the build stage), runs as a
  non-root user, base images pinned by digest and security updates applied, only server
  files copied

### Security
- Cookidoo's OAuth access and refresh tokens are masked in errors and logs, like the password
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
