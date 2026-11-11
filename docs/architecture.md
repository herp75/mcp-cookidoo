# Architecture

An MCP server (FastMCP) in front of [cookidoo-api](https://github.com/miaucl/cookidoo-api).
Tools validate their input, call one method of `CookidooService`, and turn the result into
short text for the model. Everything that talks to Cookidoo goes through the service.

## Module layout

| Module | Role |
| --- | --- |
| `server.py` | FastMCP app, server instructions, lifespan, CLI (`--transport stdio\|http`), the core recipe tools (search, details, custom recipes), the `create_thermomix_recipe` prompt; registers the tool modules |
| `tools/` | The other tool groups, one module each: `account`, `shopping_list`, `meal_plan`, `recipe_collections`, `custom_recipes` (copy, image), `thermomix` (`get_thermomix_modes`) |
| `tools/common.py` | Annotation presets (`READ_ONLY`, `WRITE`, `DESTRUCTIVE`, …), the `@tool` decorator, `service()`, `require_confirm`, ID and list helpers, `one_line` |
| `cookidoo_service.py` | `CookidooSettings` (from the environment), `CookidooService`: login and session, one method per Cookidoo operation, device resolution, the mode catalog |
| `schemas.py` | Pydantic models of the recipe JSON: `CustomRecipe`, `Step`, the settings per mode |
| `guided.py` | Steps ↔ Cookidoo annotations: writes settings into the step text (en/fr/de) and back (`to_instruction`, `from_raw`) |
| `devices.py` | TM5/TM6/TM7 limits (`MODE_RULES`), `check_steps`, `ModeCatalog`, the editor-page parser |
| `recipe_images.py` | Image path allowlist, validation and re-encoding, the signed Cloudinary upload |
| `security.py` | Secret redaction (logs and errors), ID validation, HTTP bearer-token middleware, bind checks |
| `backup.py` | Read-only backup to Markdown (`Backup`, renderers); uses `CookidooService` (`own_recipes_raw` with ETags, `catalogue_recipe_raw`, `all_collections`) and `guided.editable_recipe` |
| `prompt*.md` | The `create_thermomix_recipe` prompt in English, French and German |

## Service and tools

- Tools never hold a Cookidoo client. They call `server._get_service()` (in `tools/`:
  `service()`, imported late because `server` imports the tool modules). Tests replace
  `server._cookidoo_service` with a service on top of `FakeCookidoo` (`tests/conftest.py`),
  so every tool runs through the real MCP protocol against in-memory data.
- Service methods return the library's dataclasses; formatting stays in the tools.
- Errors: tools catch exceptions and raise `tool_error(message, e)`, a `ToolError` whose
  text went through `redact()`. Input problems are a plain `ToolError` with a hint on what
  to do instead.
- Text from Cookidoo (names, steps, notes) is untrusted: it is passed through `one_line()`
  where it sits in a line of tool output, and the server instructions tell the model to
  treat it as data.

## Authentication and session

1. `load_cookidoo_settings()` reads `COOKIDOO_*` (and `.env`) on the first tool call and
   registers the password for redaction.
2. `CookidooService.login()` picks the Cookidoo site from country and language, opens one
   `aiohttp.ClientSession` (default TLS verification) and logs in. The OAuth tokens are
   kept in memory only and registered for redaction on every login and refresh.
3. Later calls reuse the client; cookidoo-api refreshes the token. `connect_to_cookidoo`
   forces a new login. The lifespan closes the session when the server stops.

## Recipe steps and devices

```
generate_recipe_structure / upload_custom_recipe / update_custom_recipe
  └─ CustomRecipe (schemas.py)               JSON shape and value types
  └─ _check_device (server.py)
       ├─ resolve_device                     argument → recipe's devices → COOKIDOO_DEVICE
       │                                     → linked devices → TM6
       ├─ get_mode_catalog                   editor page of an own recipe, cached 1 h,
       │                                     else BUILTIN_CATALOG (it only restricts)
       ├─ check_steps (devices.py)           limits per mode and device
       └─ to_instruction (guided.py)         rendered as for the upload
  └─ create/update (cookidoo_service.py)     annotations sent to Cookidoo
```

`get_custom_recipe` goes the other way with `from_raw`, leniently: recipes already on
Cookidoo are returned whatever their values, and are checked again only when saved.

The per-mode limits are a snapshot of the Cookidoo editor's script; the weekly live run
(`tests/live/test_live_editor.py`) compares them with the live editor. See
[device-modes-analysis.md](device-modes-analysis.md).

## Adding a tool

1. Add a method to `CookidooService` if it needs a new Cookidoo call.
2. Write the tool in the matching `tools/` module with `@tool(<annotation preset>)`:
   - `READ_ONLY` if it changes nothing; `WRITE` if it only adds; `DESTRUCTIVE` if it
     deletes or overwrites (also "replace", "move", "rename"); `*_IDEMPOTENT` if
     repeating it changes nothing more.
   - Irreversible deletions (`delete_*`, `clear_*`) take `confirm: bool = False` and call
     `require_confirm`. Tools that overwrite (`update_custom_recipe`, the image upload) do
     not: they are marked destructive, say what is lost in the docstring, and the server
     instructions require the user's approval of the recipe before it is saved.
   - Validate IDs with `validate_id(s)` before they reach a URL; cap lists (`IdList`).
   - The docstring is what the model reads: one line on what it does, then its side
     effects (what is lost, whether each call creates another item), then `Args:`.
   - Catch errors with `tool_error(...)`; pass Cookidoo text through `one_line()`.
3. Tests: success and error paths against `FakeCookidoo` (`tests/test_*.py`), and, if it
   writes, a round trip in `tests/live/test_live_write.py` that removes what it created.
4. Update the contract snapshot deliberately:
   `UPDATE_SNAPSHOTS=1 uv run pytest tests/test_tool_contract.py`, and review the diff.
5. README tool table and CHANGELOG `[Unreleased]`.
