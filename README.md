# Cookidoo MCP Server

An MCP (Model Context Protocol) server for interacting with the Thermomix Cookidoo platform, built with [FastMCP](https://gofastmcp.com).

> **Disclaimer:** This is an unofficial project. The developers are not affiliated with,
> endorsed by, or connected to Cookidoo, Vorwerk, Thermomix, or any of their subsidiaries or
> trademarks. It uses Cookidoo's private web API with your own account: that may conflict with
> Cookidoo's terms of use, and it can break whenever Cookidoo changes its site (a weekly live
> test watches for that). Use it at your own risk.

## Features

- **Recipe Search**: Find recipes by keywords and/or ingredients, with a maximum total time
- **Recipe Details**: Fetch ingredients, steps and times of any Cookidoo recipe
- **Custom Recipes**: List, read, create, update and delete the recipes on your own account
- **Shopping List**: Add recipes and items, tick them off, rename, remove or clear
- **Meal Plan**: See the week, plan recipes on a day or remove them
- **Collections**: List, follow and create collections, add and remove their recipes
- **Account**: Subscription and linked Thermomix devices
- **Guided Cooking**: Steps can carry Thermomix settings (time, temperature, speed, reverse), preset on the machine
- **Guided Prompt**: `create_thermomix_recipe` walks the assistant through writing a proper Thermomix recipe (EN / FR / DE)

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env   # then fill in your Cookidoo credentials
```

| Variable | Default | Description |
| --- | --- | --- |
| `COOKIDOO_EMAIL` | — | Cookidoo account email (required) |
| `COOKIDOO_PASSWORD` | — | Cookidoo account password (required) |
| `COOKIDOO_COUNTRY` | `fr` | Cookidoo site country code (`fr`, `de`, `gb`, `es`, …) |
| `COOKIDOO_LANGUAGE` | `fr-FR` | Site language (e.g. `de-DE`); also the language guided-cooking settings are written in |
| `COOKIDOO_DEVICE` | — (detected) | Thermomix recipes are written for (`TM5`, `TM6`, `TM7`). Unset: from the devices linked to the account (TM6 rules if a TM6 is among them), else TM6 |
| `COOKIDOO_IMAGE_DIRS` | — (upload disabled) | Folders `upload_custom_recipe_image` may read images from, separated by `:` (`;` on Windows) |
| `COOKIDOO_MCP_TRANSPORT` | `stdio` | `stdio` or `http` (same as `--transport`) |
| `COOKIDOO_MCP_HOST` | `127.0.0.1` | HTTP bind address. Any non-loopback address requires `COOKIDOO_MCP_TOKEN` |
| `COOKIDOO_MCP_PORT` | `8000` | HTTP port |
| `COOKIDOO_MCP_TOKEN` | — | Bearer token for HTTP mode, at least 32 characters (e.g. `openssl rand -hex 32`) |
| `COOKIDOO_TEST_EMAIL`, `COOKIDOO_TEST_PASSWORD` | — | Live tests only: a dedicated test account (see [Live tests](#live-tests)) |
| `COOKIDOO_TEST_COUNTRY`, `COOKIDOO_TEST_LANGUAGE` | `ch`, `de-CH` | Live tests only: the test account's site |

## Usage

### Claude Code

```bash
claude mcp add cookidoo -- uv run --directory /path/to/mcp-cookidoo server.py
```

### Claude Desktop

**Bundle (easiest):** download `mcp-cookidoo-<version>.mcpb` from the
[latest release](https://github.com/herp75/mcp-cookidoo/releases/latest) and open it with
Claude Desktop. It asks for your Cookidoo e-mail, password (kept in the system's secure
storage), site and Thermomix; Claude Desktop installs Python and the dependencies itself.

**From a checkout:**

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "cookidoo": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mcp-cookidoo", "server.py"]
    }
  }
}
```

### Docker

Images for amd64 and arm64 are published to GitHub Container Registry: `edge` is built from
every change on `main`, version tags (`1`, `1.2`, `1.2.0`) from releases. Keep the credentials
in an env file (`chmod 600`) rather than in the client configuration:

```json
{
  "mcpServers": {
    "cookidoo": {
      "command": "docker",
      "args": ["run", "--rm", "-i", "--env-file", "/path/to/cookidoo.env",
               "ghcr.io/herp75/mcp-cookidoo:edge"]
    }
  }
}
```

`cookidoo.env` holds the variables of the table above (`COOKIDOO_EMAIL=…`, one per line).
For `upload_custom_recipe_image`, mount the photo folder read-only and name it in
`COOKIDOO_IMAGE_DIRS`: add `"-v", "/path/to/photos:/photos:ro", "-e", "COOKIDOO_IMAGE_DIRS=/photos"`
before the image name.
To build the image yourself: `docker build -t mcp-cookidoo .`; it runs as a non-root user.

### Verifying a release

Images are signed keyless with [cosign](https://docs.sigstore.dev/) by the Docker workflow of
this repository; release files carry GitHub build provenance:

```bash
cosign verify ghcr.io/herp75/mcp-cookidoo:2.0.0 \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp '^https://github.com/herp75/mcp-cookidoo/\.github/workflows/docker\.yml@'
gh attestation verify mcp-cookidoo-2.0.0.mcpb --repo herp75/mcp-cookidoo
```

The server is also listed in the [MCP Registry](https://registry.modelcontextprotocol.io) as
`io.github.herp75/mcp-cookidoo`.

### HTTP

```bash
uv run server.py --transport http                   # http://127.0.0.1:8000/mcp

# Reachable from other machines: a token is required, the server refuses to start without one
COOKIDOO_MCP_TOKEN=$(openssl rand -hex 32) COOKIDOO_MCP_HOST=0.0.0.0 uv run server.py --transport http
```

Clients then send `Authorization: Bearer <token>`. `GET /health` returns `{"status":"ok"}`.
With Docker, the server must bind to all interfaces inside the container, so a token is
required; publish the port on loopback only:

```bash
docker run --rm --env-file cookidoo.env -e COOKIDOO_MCP_HOST=0.0.0.0 \
  -e COOKIDOO_MCP_TOKEN="$(openssl rand -hex 32)" -p 127.0.0.1:8000:8000 \
  ghcr.io/herp75/mcp-cookidoo:edge --transport http
```

Without a token, the server only answers requests addressed to `localhost`, `127.0.0.1` or
`[::1]` (`Host` header) and, from a browser, only pages served from this machine (`Origin`):
this stops web pages from reaching it through DNS rebinding. Behind a reverse proxy or under
another host name, set a token.

Use `server.py` rather than `fastmcp run … --transport http`, which skips these checks.

### Development

```bash
uv run fastmcp dev inspector fastmcp.json           # MCP Inspector
```

## Available Tools

Type: **read** = read-only; **write** = changes the account; **destructive** = deletes or
overwrites data. Tools marked † refuse to run unless called with `confirm=true`, as a second
safety layer on top of your client's approval prompt.

| Tool | Type | Description |
| --- | --- | --- |
| **Recipes** | | |
| `search_recipes` | read | Search recipes by query, ingredients and max total time |
| `get_recipe_details` | read | Get ingredients, steps and times of a recipe by ID |
| **Custom recipes** | | |
| `list_custom_recipes` | read | List the custom recipes on your account |
| `get_custom_recipe` | read | Get a custom recipe as editable JSON |
| `generate_recipe_structure` | read | Validate a new recipe for a Thermomix (TM5/TM6/TM7) and return its JSON |
| `upload_custom_recipe` | write | Create a custom recipe from that JSON, for a Thermomix |
| `update_custom_recipe` | destructive | Replace an existing custom recipe with that JSON |
| `copy_recipe_to_custom` | write | Copy a catalogue recipe into a new custom recipe, to adapt it |
| `upload_custom_recipe_image` | destructive | Set a custom recipe's photo from a local image (see `COOKIDOO_IMAGE_DIRS`) |
| `delete_custom_recipe` † | destructive | Permanently delete a custom recipe |
| **Shopping list** | | |
| `get_shopping_list` | read | Recipes, ingredients and hand-added items, with ticked state and IDs |
| `add_recipes_to_shopping_list` | write | Add the ingredients of catalogue and/or custom recipes |
| `remove_recipes_from_shopping_list` | destructive | Remove recipes and their ingredients |
| `add_shopping_list_items` | write | Add items by hand ("milk", "2 lemons") |
| `edit_shopping_list_items` | destructive | Rename items added by hand |
| `mark_shopping_list_items` | write | Tick items off, or put them back |
| `remove_shopping_list_items` | destructive | Delete items added by hand |
| `clear_shopping_list` † | destructive | Empty the whole shopping list |
| **Meal plan** | | |
| `get_meal_plan_week` | read | The planned recipes of the 7 days starting at a day |
| `add_to_meal_plan` | write | Plan catalogue and/or custom recipes on a day |
| `move_in_meal_plan` | destructive | Move a planned recipe to another day (rolled back on failure) |
| `remove_from_meal_plan` | destructive | Remove recipes from a day (the recipes are kept) |
| **Collections** | | |
| `list_collections` | read | Your own and followed collections with their recipes (paginated) |
| `follow_collection` / `unfollow_collection` | write / destructive | Follow or unfollow a Cookidoo collection |
| `create_collection` | write | Create a custom collection |
| `delete_collection` † | destructive | Delete a custom collection (its recipes are kept) |
| `add_to_collection` / `remove_from_collection` | write / destructive | Add or remove recipes in a custom collection |
| **Thermomix** | | |
| `get_thermomix_modes` | read | Settings and modes a device supports, with limits and examples |
| **Account** | | |
| `connect_to_cookidoo` | read | Check the configuration / force a fresh login (optional, tools log in automatically) |
| `get_account_info` | read | User, Cookidoo site, subscription and devices |
| `list_devices` | read | Thermomix devices linked to the account |

## Recipe steps

A step is either plain text, or an object with Thermomix settings preset in guided cooking
and/or ingredient links. The text holds one `{settings}` placeholder per setting, replaced
by the setting written out in the account's language (`COOKIDOO_LANGUAGE`): French,
English and German are supported, other languages are written in English. The settings preset
on the Thermomix are the same in every language.

```json
{
  "text": "Add the onion and the olive oil, then sauté {settings}.",
  "settings": [{"time": 180, "temperature": "120", "speed": "1", "reverse": true}],
  "ingredients": [{"text": "the onion", "ingredient": "1 onion, halved"}]
}
```

A setting's `mode` selects its fields. The limits are those of the Cookidoo recipe editor
(snapshot of cookidoo.de, 2026-10-20), and they depend on the Thermomix the recipe is for:

| `mode` | Devices | Fields and limits | Written as (fr) | Written as (de) |
| --- | --- | --- | --- | --- |
| `manual` (default) | TM5, TM6, TM7 | `time` (1 s – 99 min), `temperature` (optional: `"37"`…`"95"` by 5, `"98"`, `"100"`…`"120"` by 5, or `"varoma"` on the TM6 only), `speed` (at most `"6"` with a temperature), `reverse` | `3 min/120°C/vitesse 1` | `3 Min./120°C/Stufe 1` |
| `dough` | TM6, TM7 | `time` (up to 20 min) | `Pétrin/2 min` | `Teig kneten/2 Min.` |
| `browning` | TM7 | `time` (up to 30 min), `temperature` (`"140"`…`"160"` by 5), `power` (`gentle`, `intense`) | `Rissolage/5 min/160°C/intense` | `Anbraten/5 Min./160°C/Intensiv` |
| `steaming` | TM7 | `time` (up to 99 min), `speed` (up to `"5"`), `reverse`, `accessory` (`varoma`, `simmering_basket`, `varoma_and_simmering_basket`) | `Cuisson vapeur/20 min/Varoma/vitesse 1` | `Dampfgaren/20 Min./im Varoma/Stufe 1` |
| `turbo` | TM6, TM7 | `time` (0.5, 1 or 2 s), `pulses` (1 to 9, TM7 only) | `Turbo/0.5 sec/3 fois` | `Turbo/0.5 Sek./3x` |
| `rice_cooker` | TM6, TM7 | none | `Rice cooker` | `Reis kochen` |
| `warm_up` | TM6, TM7 | `temperature` (`"37"`…`"90"`), `speed` (`"soft"`, `"1"`, `"2"`; no time: Cookidoo drops it) | `Réchauffage/65°C/vitesse 2` | `Erwärmen/65°C/Stufe 2` |
| `blend` | TM6, TM7 | `time` (10 s – 5 min), `speed` (`"6"`…`"8"`) | `Mixage/1 min/vitesse 8` | `Pürieren/1 Min./Stufe 8` |

`speed` is `"soft"` (stirring speed, `Sanftrührstufe`) or `"0.5"` to `"10"` by halves
(written `Stufe 0,5` in German). Cookidoo has no other mode for custom recipes (sous-vide,
slow cooking, fermentation… only exist in catalogue recipes).

**Target device.** `generate_recipe_structure`, `upload_custom_recipe` and
`update_custom_recipe` take an optional `device` (`TM5`, `TM6`, `TM7`) and refuse settings that
device does not support, naming the allowed values. Without it: for an update, the recipe's
own devices; then `COOKIDOO_DEVICE`; then the devices linked to the account (TM6 rules if a
TM6 is among them, as TM6-valid steps also run on a TM7); then TM6. Times off the editor's
grid (10 s steps up to 5 min, 30 s up to 20 min, then whole minutes) only give a warning.
Which modes exist and which are TM7-only is read from the Cookidoo recipe editor of your
account (cached for an hour), with the built-in snapshot as fallback; the tool output says
which was used. Recipes already on Cookidoo are always readable, whatever their values, and
are only checked when saved again.

Modes are written with their name, so the text should not repeat it ("then start
{settings}" rather than "knead {settings}").

`ingredients` links words of the step text (their first occurrence) to a line of the
ingredient list. Invalid settings and links are rejected. `get_custom_recipe` returns steps
in the same format, so a recipe keeps its settings and links when edited.

## Backup

`backup.py` copies the account to Markdown files: your own recipes, your collections and the
catalogue recipes in them. It only reads from Cookidoo. Each run downloads only what changed:

- Own recipes: one request for all of them. Cookidoo answers with an empty `304 Not Modified`
  when nothing changed since the last run.
- Collections: their lists, a few small requests.
- Catalogue recipes: each new one once (about 4 KB). A copy older than 90 days is downloaded
  again, at most 1/12 of them per run.

Files are only rewritten when their content changes, and nothing is deleted: a recipe that
disappears from Cookidoo or from all collections keeps its file, marked as such. Each own recipe
also gets a `.json` file, with the recipe in the format `upload_custom_recipe` accepts (to
upload it again) and exactly as Cookidoo returned it.

```
cookidoo-backup/
  README.md                       index
  own/<name>--<id>.md, .json      your recipes
  catalogue/<name>--<id>.md       recipes from your collections
  collections/<name>--<id>.md     chapters, with links to the recipe files
```

`scripts/cookidoo-backup.sh` runs it with the Docker image and commits the changes to a git
repository, so the history shows what changed and when. It uses the same env file as the
server (default `~/.config/cookidoo-mcp/cookidoo.env`):

```bash
scripts/cookidoo-backup.sh ~/cookidoo-backup     # podman, or docker if podman is missing
```

To run it weekly, copy `scripts/systemd/cookidoo-backup.{service,timer}` to
`~/.config/systemd/user/`, adjust the path in the service, and
`systemctl --user enable --now cookidoo-backup.timer`. Without Docker:
`uv run backup.py ~/cookidoo-backup` (reads `.env` like the server).

Catalogue recipes are Vorwerk's content: keep the backup private, never in a public repository.

## Security

- The server stores your real Cookidoo password (Cookidoo has no OAuth): use a unique password.
- Prefer stdio. Use HTTP only with a token, on a trusted network.
- TLS certificate verification is always on; secrets are redacted from logs and error messages.
- Recipe content is untrusted input to the model. Keep your client's approval prompt on for
  write tools.
- `upload_custom_recipe_image` is the only tool that reads a local file and sends it to the
  internet. It is off until you set `COOKIDOO_IMAGE_DIRS`: point it at a folder that holds
  only recipe photos, not your home directory. Files outside it (also through symlinks) and
  anything but JPEG/PNG/WebP up to 10 MB are refused; images are re-encoded and their
  metadata (EXIF, GPS location) removed before upload. With Docker, mount that folder
  read-only (`-v ~/RecipePhotos:/photos:ro -e COOKIDOO_IMAGE_DIRS=/photos`).

See [SECURITY.md](SECURITY.md) for the threat model and how to report a vulnerability.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow and
[docs/architecture.md](docs/architecture.md) for how the code is organised.

```bash
uv sync
uv run pytest --cov                    # tests, coverage must stay at 85 % or more
uv run ruff check . && uv run ruff format .
uv run pyright
scripts/check_insecure_patterns.sh     # TLS / bind-address guard, also run in CI
```

CI also runs pip-audit, gitleaks, CodeQL, dependency review and a Trivy scan of the Docker image.

### Live tests

`tests/live` runs against the real Cookidoo API. Use a **dedicated test account**, never your
own: the write tests create and delete recipes, a collection, shopping-list items and meal-plan
entries (all named "MCP live test …").

```bash
cp .env.live.example .env.live && chmod 600 .env.live   # then fill in the test account
uv run pytest tests/live --live -p no:logging --assert=plain        # read-only
uv run pytest tests/live --live-write -p no:logging --assert=plain  # also writes, cleans up
```

They read only `COOKIDOO_TEST_*` and refuse to run with the account in `COOKIDOO_EMAIL`.
The "Live tests" workflow runs them weekly with the secrets `COOKIDOO_TEST_EMAIL` and
`COOKIDOO_TEST_PASSWORD` of the `live` environment (deployment branch `main` only; optional
variables `COOKIDOO_TEST_COUNTRY`, `COOKIDOO_TEST_LANGUAGE`, default `ch` / `de-CH`). It also
compares the Cookidoo recipe editor with `devices.py` and opens an issue if they differ.

## Credits

- [alexandrepa/mcp-cookidoo](https://github.com/alexandrepa/mcp-cookidoo) by Alexandre
  Patelli: the original server this fork extends.
- [tobilektri/mcp-cookidoo](https://github.com/tobilektri/mcp-cookidoo) (MIT): German
  Thermomix notation, mode limits per device, the editor-page parser and fixtures.
- [vitaliemiron/cookidoo-mcp](https://github.com/vitaliemiron/cookidoo-mcp) (Apache-2.0):
  recipe image upload and the meal-plan move; see [NOTICE](NOTICE).
- [danielkliem/mcp-cookidoo](https://github.com/danielkliem/mcp-cookidoo) and
  [sisques-labs/cookidoo-mcp](https://github.com/sisques-labs/cookidoo-mcp): feature
  inspiration only, no code copied.
- [cookidoo-api](https://github.com/miaucl/cookidoo-api) by miaucl: the Python client for the
  Cookidoo platform everything here is built on.
- Everyone who opened pull requests on the original repository: their ideas (search, recipe
  editing, Docker/STDIO support) shaped it.

## License

MIT, see [LICENSE](LICENSE) and [NOTICE](NOTICE).
