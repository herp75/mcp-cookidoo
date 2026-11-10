"""
Cookidoo Service

Module to encapsulate all cookidoo-api logic for interacting with the Cookidoo platform.

SECURITY: the aiohttp ClientSession below must keep its default TLS certificate
verification. Never turn off the ssl argument, pass a permissive SSLContext or use a
connector that skips certificate checks: the Cookidoo password is sent over this
connection. CI fails on such code.

Login tokens and cookies only live in memory, inside this session. If they are ever cached
on disk, write them with mode 0600 under the user's config directory, never in the repo.
"""

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, cast

from aiohttp import ClientSession, ClientTimeout
from cookidoo_api import (
    Cookidoo,
    CookidooAdditionalItem,
    CookidooAuthData,
    CookidooAuthException,
    CookidooCollection,
    CookidooConfig,
    CookidooCreateCustomRecipe,
    CookidooCustomRecipe,
    CookidooDevice,
    CookidooIngredientItem,
    CookidooRequestException,
    CookidooSearchResult,
    CookidooShoppingRecipe,
    CookidooShoppingRecipeDetails,
    CookidooSubscription,
    CookidooUpdateCustomRecipe,
    CookidooUserInfo,
    ThermomixMachineType,
)
from cookidoo_api.const import CUSTOM_RECIPES_PATH_ACCEPT
from cookidoo_api.helpers import (
    cookidoo_custom_recipe_from_json,
    cookidoo_search_result_from_json,
    get_localization_options,
)
from cookidoo_api.types import CookidooCalendarDay
from dotenv import load_dotenv
from yarl import URL

import recipe_images
from devices import (
    BUILTIN_CATALOG,
    DEFAULT_DEVICE,
    DEVICES,
    MAX_EDITOR_HTML,
    Device,
    ModeCatalog,
    parse_editor_html,
)
from guided import to_instruction
from schemas import CustomRecipe
from security import redact, register_secret


@dataclass(frozen=True)
class CookidooSettings:
    """Settings loaded from the environment / .env file."""

    email: str
    password: str
    country: str = "fr"
    language: str = "fr-FR"
    device: Device | None = None  # COOKIDOO_DEVICE; None: detect from the account


CATALOG_TTL = 3600  # seconds a mode catalog read from Cookidoo is reused
FALLBACK_TTL = 300  # seconds the built-in catalog is used after the editor could not be read
RETIRE_DELAY = 60  # seconds a replaced session stays open for calls still using it
MAX_RAW_JSON = 20 * 1024 * 1024  # bytes: a response for the backup larger than this is refused
MAX_COLLECTION_PAGES = 100
LOGIN_BACKOFF = 60  # seconds a refused automatic login is not retried (no account lockout)


def pick_device(devices: list[str]) -> Device | None:
    """
    The device whose rules a recipe for these devices must follow: TM6 when a TM6 is among
    them (TM6-valid steps also run on a TM7), else TM7, else TM5.
    """
    for device in ("TM6", "TM7", "TM5"):
        if device in devices:
            return device
    return None


def load_cookidoo_settings() -> CookidooSettings:
    """
    Load Cookidoo settings from the environment (and .env file if present).

    Returns:
        CookidooSettings: Credentials, localization and default device

    Raises:
        ValueError: If credentials are missing or the device is unknown
    """
    load_dotenv()

    email = os.getenv("COOKIDOO_EMAIL")
    password = os.getenv("COOKIDOO_PASSWORD")
    if not email or not password:
        raise ValueError(
            "Missing Cookidoo credentials. Please set COOKIDOO_EMAIL and "
            "COOKIDOO_PASSWORD in your environment or .env file"
        )

    register_secret(password)

    device = (os.getenv("COOKIDOO_DEVICE") or "").strip().upper() or None
    if device is not None and device not in DEVICES:
        raise ValueError(f"Unknown COOKIDOO_DEVICE '{device}'. Use one of: {', '.join(DEVICES)}")

    return CookidooSettings(
        email=email,
        password=password,
        country=(os.getenv("COOKIDOO_COUNTRY") or "fr").strip().lower(),
        language=(os.getenv("COOKIDOO_LANGUAGE") or "fr-FR").strip(),
        device=cast(Device | None, device),
    )


def _register_tokens(auth: CookidooAuthData) -> None:
    """Mask the OAuth tokens of every login and refresh in errors and logs, like the password."""
    register_secret(auth.access_token)
    register_secret(auth.refresh_token)


class MoveError(Exception):
    """A meal-plan move failed after the recipe was added to the new day."""

    def __init__(
        self, error: BaseException, rolled_back: bool, rollback_error: BaseException | None = None
    ):
        super().__init__(str(error))
        self.error = error
        self.rolled_back = rolled_back
        self.rollback_error = rollback_error


class CookidooService:
    """Service class for managing Cookidoo API interactions."""

    def __init__(self, settings: CookidooSettings):
        self.settings = settings
        self._api_client: Cookidoo | None = None
        self._session: ClientSession | None = None
        self._lock = asyncio.Lock()
        self._catalog: ModeCatalog | None = None
        self._catalog_time = 0.0
        self._catalog_ttl = CATALOG_TTL
        self._retiring: dict[asyncio.Task, ClientSession] = {}
        self._login_refused: tuple[float, CookidooAuthException] | None = None

    async def login(self) -> Cookidoo:
        """
        Authenticate with Cookidoo (again) and return the API client.

        The new session replaces the current one only once the login succeeded; a failed
        login leaves a working session in place.

        Raises:
            ValueError: If the configured country/language is not supported
            CookidooException: If authentication fails
        """
        async with self._lock:
            return await self._login()

    async def _login(self) -> Cookidoo:
        """Log in with a new session and swap it in. Call with the lock held."""
        localizations = await get_localization_options(
            country=self.settings.country, language=self.settings.language
        )
        if not localizations:
            raise ValueError(
                f"No Cookidoo site for country '{self.settings.country}' and "
                f"language '{self.settings.language}'"
            )

        session = ClientSession()  # default TLS verification: keep it that way
        try:
            client = Cookidoo(
                session=session,
                cfg=CookidooConfig(
                    email=self.settings.email,
                    password=self.settings.password,
                    localization=localizations[0],
                ),
                on_auth_data_update=_register_tokens,
            )
            await client.login()
        except BaseException:
            await session.close()
            raise

        old, self._session, self._api_client = self._session, session, client
        self._login_refused = None
        if old is not None:
            # Calls that started before the re-login may still be using the old session
            task = asyncio.create_task(self._close_later(old))
            self._retiring[task] = old
            task.add_done_callback(lambda done: self._retiring.pop(done, None))
        return client

    @staticmethod
    async def _close_later(session: ClientSession) -> None:
        try:
            await asyncio.sleep(RETIRE_DELAY)
        finally:
            await session.close()

    async def _automatic_login(self) -> Cookidoo:
        """
        _login for logins nobody asked for. After Cookidoo refused the credentials, repeat
        that answer for a minute instead of trying again on every call. Call with the lock.
        """
        if self._login_refused:
            since, error = self._login_refused
            if time.monotonic() - since < LOGIN_BACKOFF:
                raise CookidooAuthException(str(error))
        try:
            return await self._login()
        except CookidooAuthException as error:
            self._login_refused = (time.monotonic(), error)
            raise

    async def client(self) -> Cookidoo:
        """
        Return the authenticated client, logging in on first use. Concurrent first calls
        share one login: without the lock, each would log in and close the session the
        others were using ("Session is closed").
        """
        if (client := self._api_client) is not None and not client._is_token_expiring():
            return client
        async with self._lock:
            if self._api_client is None:
                return await self._automatic_login()
            try:
                # Refresh now rather than inside the next request: when Cookidoo no longer
                # accepts the refresh token, log in again instead of failing every call
                await self._api_client._ensure_token()
            except CookidooAuthException:
                return await self._automatic_login()
            return self._api_client

    async def _close_session(self) -> None:
        self._api_client = None
        retiring, self._retiring = self._retiring, {}
        for task, session in retiring.items():
            task.cancel()
            await session.close()
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def close(self) -> None:
        """Close the aiohttp session."""
        async with self._lock:
            await self._close_session()

    async def search_recipes(
        self,
        query: str | None = None,
        ingredients: list[str] | None = None,
        max_total_time: int | None = None,
        max_results: int = 10,
    ) -> CookidooSearchResult:
        """
        Search the Cookidoo catalogue. `max_total_time` is in minutes.

        Calls the search endpoint directly rather than Cookidoo.search_recipes: the
        API expects one `ingredients` parameter per ingredient (a comma-joined value
        returns nothing), `limit` rather than `pageSize`, and `languages` to keep
        results in the account's language.
        """
        api = await self.client()
        locale = api.localization.language.split("-")[0]
        params: list[tuple[str, str]] = [("languages", locale)]
        if query:
            params.append(("query", query))
        params += [("ingredients", ingredient) for ingredient in ingredients or []]
        if max_total_time:
            params.append(("totalTime", str(max_total_time * 60)))
        params.append(("limit", str(max_results)))

        await api._ensure_endpoints()
        url = api.api_endpoint / api._path("search:home").format(locale=locale)
        # The library types params as a dict, but aiohttp accepts (and we need) repeated keys
        result: Any = await api._request_json(
            "get", url, "search recipes", params=cast(Any, params)
        )
        if not result:
            return CookidooSearchResult(recipes=[], total=0)
        return cookidoo_search_result_from_json(result, api.localization)

    async def get_recipe_details(self, recipe_id: str) -> CookidooShoppingRecipeDetails:
        api = await self.client()
        return await api.get_recipe_details(recipe_id)

    async def list_custom_recipes(self) -> list[CookidooCustomRecipe]:
        api = await self.client()
        return await api.list_custom_recipes()

    async def get_custom_recipe(self, recipe_id: str) -> tuple[CookidooCustomRecipe, list]:
        """
        A custom recipe, with its steps as Cookidoo's raw JSON.

        cookidoo-api parses the recipe but drops annotation fields it does not model (turbo
        pulses, sub-second times), so the steps are read from the same response.
        """
        api = await self.client()
        await api._ensure_endpoints()
        url = api.api_endpoint / api._path("customer-recipes:recipe-details").format(
            **api.localization.__dict__, id=recipe_id
        )
        result: Any = await api._request_json(
            "get", url, "loading custom recipe", headers={"ACCEPT": CUSTOM_RECIPES_PATH_ACCEPT}
        )
        recipe = cookidoo_custom_recipe_from_json(result, api.localization)
        steps = result["recipeContent"].get("instructions") or [
            step if isinstance(step, str) else step.text for step in recipe.instructions
        ]
        return recipe, list(steps)

    async def create_custom_recipe(
        self, recipe: CustomRecipe, device: Device = DEFAULT_DEVICE
    ) -> CookidooCustomRecipe:
        """Create a brand new custom recipe on the user's account, for the given device."""
        api = await self.client()
        return await api.create_custom_recipe(
            CookidooCreateCustomRecipe(
                name=recipe.name,
                ingredients=recipe.ingredients,
                instructions=[
                    to_instruction(step, self.settings.language) for step in recipe.steps
                ],
                serving_size=recipe.servings,
                active_time=recipe.prep_time * 60,
                total_time=recipe.total_time * 60,
                tools=[ThermomixMachineType(device)],
                hints=recipe.hints or [],
            )
        )

    async def update_custom_recipe(
        self, recipe_id: str, recipe: CustomRecipe
    ) -> CookidooCustomRecipe:
        """Replace the content of an existing custom recipe."""
        api = await self.client()
        return await api.update_custom_recipe(
            recipe_id,
            CookidooUpdateCustomRecipe(
                name=recipe.name,
                ingredients=recipe.ingredients,
                instructions=[
                    to_instruction(step, self.settings.language) for step in recipe.steps
                ],
                serving_size=recipe.servings,
                active_time=recipe.prep_time * 60,
                total_time=recipe.total_time * 60,
                hints=recipe.hints or [],
            ),
        )

    async def delete_custom_recipe(self, recipe_id: str) -> None:
        api = await self.client()
        await api.remove_custom_recipe(recipe_id)

    async def copy_recipe_to_custom(
        self, recipe_id: str, servings: int | None
    ) -> CookidooCustomRecipe:
        """Copy a catalogue recipe into a new custom recipe (default: its own serving size)."""
        api = await self.client()
        if servings is None:
            servings = (await api.get_recipe_details(recipe_id)).serving_size
        return await api.add_custom_recipe_from(recipe_id, servings)

    async def upload_custom_recipe_image(
        self, recipe_id: str, image_path: str
    ) -> recipe_images.UploadedImage:
        """Validate and re-encode a local image, then set it as the recipe's image."""
        jpeg, filename = recipe_images.prepare_image(image_path)  # before any network call
        api = await self.client()
        assert self._session is not None  # set by login(); default TLS verification
        return await recipe_images.upload_recipe_image(
            api, self._session, recipe_id, jpeg, filename
        )

    # --- Devices and modes ------------------------------------------------------------------

    async def resolve_device(
        self, explicit: Device | None = None, recipe_id: str | None = None
    ) -> tuple[Device, str]:
        """
        The device whose rules apply, and where that choice came from: the explicit device,
        else the recipe's own devices (when updating), else COOKIDOO_DEVICE, else the devices
        linked to the account, else TM6. Cookidoo errors fall back to the next source.
        """
        if explicit:
            return explicit, "given"
        if recipe_id:
            try:
                recipe, _ = await self.get_custom_recipe(recipe_id)
                tools = [getattr(t, "value", str(t)).upper() for t in recipe.tools or []]
                if device := pick_device(tools):
                    return device, "the recipe's devices"
            except Exception:
                pass
        if self.settings.device:
            return self.settings.device, "COOKIDOO_DEVICE"
        try:
            linked = [d.type.value for d in await self.get_devices()]
            if device := pick_device(linked):
                return device, "the devices linked to the account"
        except Exception:
            return DEFAULT_DEVICE, "default (linked devices could not be read)"
        return DEFAULT_DEVICE, "default"

    async def get_mode_catalog(self, recipe_id: str | None = None) -> ModeCatalog:
        """
        The modes Cookidoo's recipe editor offers, read from the editor page of one of the
        user's own recipes and cached for an hour. Never raises: on any problem the built-in
        catalog is returned, with a note saying why.
        """
        if self._catalog and time.monotonic() - self._catalog_time < self._catalog_ttl:
            return self._catalog
        try:
            api = await self.client()
            if recipe_id is None:
                recipes = await api.list_custom_recipes()
                if not recipes:
                    return replace(
                        BUILTIN_CATALOG,
                        note="built-in list: no own recipe yet to read the editor from",
                    )
                recipe_id = recipes[0].id
            catalog = parse_editor_html(await self.editor_html(recipe_id))
        except Exception as e:
            # Remember the fallback for a while: no new login and page read on every check
            fallback = replace(
                BUILTIN_CATALOG,
                note=redact(f"built-in list: the Cookidoo editor could not be read ({e})"),
            )
            self._catalog, self._catalog_time = fallback, time.monotonic()
            self._catalog_ttl = FALLBACK_TTL
            return fallback
        self._catalog, self._catalog_time = catalog, time.monotonic()
        self._catalog_ttl = CATALOG_TTL
        return catalog

    async def editor_html(self, recipe_id: str) -> str:
        """The Cookidoo recipe editor page of one of the user's recipes (at most 2 MB + 1)."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", recipe_id):
            raise ValueError("unexpected recipe ID")
        api = await self.client()
        assert self._session is not None
        url = (
            URL(api.localization.url).origin()
            / "created-recipes"
            / (api.localization.language)
            / recipe_id
            / "edit"
            / "ingredients-and-preparation-steps"
        )
        headers = {**api._api_headers, "ACCEPT": "text/html"}
        async with self._session.get(
            url, headers=headers, timeout=ClientTimeout(total=20), allow_redirects=False
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"editor page returned {response.status}")
            # read() returns what has arrived so far: read the chunks up to the limit
            body = bytearray()
            async for chunk in response.content.iter_chunked(64 * 1024):
                body += chunk
                if len(body) > MAX_EDITOR_HTML:
                    break
        return bytes(body[: MAX_EDITOR_HTML + 1]).decode("utf-8", errors="replace")

    # --- Raw JSON, for the backup -----------------------------------------------------------

    async def _get_json(
        self, url: URL, accept: str, etag: str | None = None
    ) -> tuple[Any | None, str | None]:
        """
        GET a JSON document. With an ETag from an earlier response, an unchanged document is
        not downloaded again: returns (None, etag) then. Otherwise (the document, its ETag).
        """
        api = await self.client()
        assert self._session is not None
        headers = {**api._api_headers, "ACCEPT": accept}
        if etag:
            headers["If-None-Match"] = etag
        async with self._session.get(
            url, headers=headers, timeout=ClientTimeout(total=60), allow_redirects=False
        ) as response:
            if response.status == 304 and etag:
                return None, etag
            if response.status == 401:
                raise CookidooAuthException("Cookidoo refused the login token (401)")
            if response.status != 200:
                raise CookidooRequestException(f"Cookidoo returned {response.status}")
            body = bytearray()
            async for chunk in response.content.iter_chunked(64 * 1024):
                body += chunk
                if len(body) > MAX_RAW_JSON:
                    raise CookidooRequestException("response too large")
            return json.loads(body), response.headers.get("ETag")

    async def own_recipes_raw(
        self, etag: str | None = None
    ) -> tuple[list[dict] | None, str | None]:
        """
        All custom recipes as Cookidoo's raw JSON, in one request (Cookidoo keeps at most 300
        per account and lists them in full). (None, etag) if nothing changed since that ETag.
        """
        api = await self.client()
        await api._ensure_endpoints()
        url = api.api_endpoint / api._path("customer-recipes:recipe-create").format(
            **api.localization.__dict__
        )
        result, new_etag = await self._get_json(url, CUSTOM_RECIPES_PATH_ACCEPT, etag)
        if result is None:
            return None, new_etag
        items = result.get("items") if isinstance(result, dict) else None
        if not isinstance(items, list):
            raise CookidooRequestException("unexpected custom recipe list")
        return [item for item in items if isinstance(item, dict)], new_etag

    async def catalogue_recipe_raw(self, recipe_id: str) -> dict:
        """A catalogue recipe as Cookidoo's raw JSON (Cookidoo sends no ETag for these)."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", recipe_id):
            raise ValueError("unexpected recipe ID")
        api = await self.client()
        await api._ensure_endpoints()
        url = api.api_endpoint / api._path("recipe:details").format(
            **api.localization.__dict__, id=recipe_id
        )
        result, _ = await self._get_json(url, "application/json")
        if not isinstance(result, dict):
            raise CookidooRequestException("unexpected recipe")
        return result

    async def all_collections(self, managed: bool) -> list[CookidooCollection]:
        """Every followed (managed) or own collection, all pages."""
        collections, page, pages = [], 0, 1
        while page < min(pages, MAX_COLLECTION_PAGES):
            batch, _, pages = await self.list_collections(managed, page)
            collections += batch
            page += 1
        return collections

    # --- Account ---------------------------------------------------------------------------

    async def get_account(self) -> tuple[CookidooUserInfo, CookidooSubscription | None]:
        api = await self.client()
        return await api.get_user_info(), await api.get_active_subscription()

    async def get_devices(self) -> list[CookidooDevice]:
        api = await self.client()
        return await api.get_devices()

    # --- Shopping list ---------------------------------------------------------------------

    async def get_shopping_list(
        self,
    ) -> tuple[
        list[CookidooShoppingRecipe], list[CookidooIngredientItem], list[CookidooAdditionalItem]
    ]:
        """Recipes on the shopping list, their ingredient items, and the additional items."""
        api = await self.client()
        return (
            await api.get_shopping_list_recipes(),
            await api.get_ingredient_items(),
            await api.get_additional_items(),
        )

    async def add_recipes_to_shopping_list(
        self, recipe_ids: list[str], custom_recipe_ids: list[str]
    ) -> list[CookidooIngredientItem]:
        api = await self.client()
        items: list[CookidooIngredientItem] = []
        if recipe_ids:
            items += await api.add_ingredient_items_for_recipes(recipe_ids)
        if custom_recipe_ids:
            items += await api.add_ingredient_items_for_custom_recipes(custom_recipe_ids)
        return items

    async def remove_recipes_from_shopping_list(
        self, recipe_ids: list[str], custom_recipe_ids: list[str]
    ) -> None:
        api = await self.client()
        if recipe_ids:
            await api.remove_ingredient_items_for_recipes(recipe_ids)
        if custom_recipe_ids:
            await api.remove_ingredient_items_for_custom_recipes(custom_recipe_ids)

    async def add_shopping_list_items(self, names: list[str]) -> list[CookidooAdditionalItem]:
        api = await self.client()
        return await api.add_additional_items(names)

    async def rename_shopping_list_items(
        self, names_by_id: dict[str, str]
    ) -> list[CookidooAdditionalItem]:
        """Rename additional items. Raises KeyError for IDs that are not additional items."""
        api = await self.client()
        current = {item.id: item for item in await api.get_additional_items()}
        missing = [item_id for item_id in names_by_id if item_id not in current]
        if missing:
            raise KeyError(missing)
        return await api.edit_additional_items(
            [replace(current[item_id], name=name) for item_id, name in names_by_id.items()]
        )

    async def mark_shopping_list_items(
        self, item_ids: list[str], owned: bool
    ) -> list[CookidooIngredientItem | CookidooAdditionalItem]:
        """
        Tick or untick items, whether they come from a recipe or were added by hand.
        Raises KeyError for IDs that are not on the shopping list.
        """
        api = await self.client()
        ingredients = {item.id: item for item in await api.get_ingredient_items()}
        additional = {item.id: item for item in await api.get_additional_items()}
        missing = [i for i in item_ids if i not in ingredients and i not in additional]
        if missing:
            raise KeyError(missing)
        changed: list[CookidooIngredientItem | CookidooAdditionalItem] = []
        if picked := [
            replace(ingredients[i], is_owned=owned) for i in item_ids if i in ingredients
        ]:
            changed += await api.edit_ingredient_items_ownership(picked)
        if picked_extra := [
            replace(additional[i], is_owned=owned) for i in item_ids if i in additional
        ]:
            changed += await api.edit_additional_items_ownership(picked_extra)
        return changed

    async def remove_shopping_list_items(self, item_ids: list[str]) -> None:
        api = await self.client()
        await api.remove_additional_items(item_ids)

    async def clear_shopping_list(self) -> None:
        api = await self.client()
        await api.clear_shopping_list()

    # --- Meal plan -------------------------------------------------------------------------

    async def get_meal_plan_week(self, day: date) -> list[CookidooCalendarDay]:
        """
        The planned days of the 7 days starting at `day` (Cookidoo leaves empty days out),
        with custom recipes resolved. Each day's id is its ISO date.
        """
        api = await self.client()
        return await api.get_recipes_in_calendar_week(day, resolve_custom_recipes=True)

    async def add_to_meal_plan(
        self, day: date, recipe_ids: list[str], custom_recipe_ids: list[str]
    ) -> None:
        api = await self.client()
        if recipe_ids:
            await api.add_recipes_to_calendar(day, recipe_ids)
        if custom_recipe_ids:
            await api.add_custom_recipes_to_calendar(day, custom_recipe_ids)

    async def remove_from_meal_plan(
        self, day: date, recipe_ids: list[str], custom_recipe_ids: list[str]
    ) -> None:
        api = await self.client()
        for recipe_id in recipe_ids:
            await api.remove_recipe_from_calendar(day, recipe_id)
        for recipe_id in custom_recipe_ids:
            await api.remove_custom_recipe_from_calendar(day, recipe_id)

    async def move_in_meal_plan(self, recipe_id: str, from_day: date, to_day: date) -> bool:
        """
        Move a planned recipe to another day. Returns whether it is a custom recipe.

        Adds to the new day first, then removes from the old one, so a failure never loses
        the recipe: if the removal fails, the addition is rolled back. If the recipe is
        already planned on the new day, it is only removed from the old one (and a rollback
        never touches the entry that was there before).

        Raises:
            LookupError: if the recipe is not planned on from_day
            MoveError: if the move failed (and whether the rollback worked)
        """
        api = await self.client()

        async def planned(day: date) -> tuple[bool, bool]:
            """Whether the recipe is planned on that day, and whether as a custom recipe."""
            week = await api.get_recipes_in_calendar_week(day)
            entry = {d.id: d for d in week}.get(day.isoformat())
            if entry is None:
                return False, False
            if recipe_id in entry.customer_recipe_ids or any(
                r.id == recipe_id and r.is_custom for r in entry.recipes
            ):
                return True, True
            return any(r.id == recipe_id for r in entry.recipes), False

        found, is_custom = await planned(from_day)
        if not found:
            raise LookupError(recipe_id)
        already_there, _ = await planned(to_day)

        add = api.add_custom_recipes_to_calendar if is_custom else api.add_recipes_to_calendar
        remove = (
            api.remove_custom_recipe_from_calendar if is_custom else api.remove_recipe_from_calendar
        )
        if not already_there:
            await add(to_day, [recipe_id])
        try:
            await remove(from_day, recipe_id)
        except Exception as error:
            if already_there:
                raise MoveError(error, rolled_back=True) from error
            try:
                await remove(to_day, recipe_id)
            except Exception as rollback_error:
                raise MoveError(error, rolled_back=False, rollback_error=rollback_error) from error
            raise MoveError(error, rolled_back=True) from error
        return is_custom

    # --- Collections -----------------------------------------------------------------------

    async def list_collections(
        self, managed: bool, page: int
    ) -> tuple[list[CookidooCollection], int, int]:
        """One page of followed (managed) or custom collections, with total count and pages."""
        api = await self.client()
        if managed:
            count, pages = await api.count_managed_collections()
            return await api.get_managed_collections(page), count, pages
        count, pages = await api.count_custom_collections()
        return await api.get_custom_collections(page), count, pages

    async def follow_collection(self, collection_id: str) -> CookidooCollection:
        api = await self.client()
        return await api.add_managed_collection(collection_id)

    async def unfollow_collection(self, collection_id: str) -> None:
        api = await self.client()
        await api.remove_managed_collection(collection_id)

    async def create_collection(self, name: str) -> CookidooCollection:
        api = await self.client()
        return await api.add_custom_collection(name)

    async def delete_collection(self, collection_id: str) -> None:
        api = await self.client()
        await api.remove_custom_collection(collection_id)

    async def add_to_collection(
        self, collection_id: str, recipe_ids: list[str]
    ) -> CookidooCollection:
        api = await self.client()
        return await api.add_recipes_to_custom_collection(collection_id, recipe_ids)

    async def remove_from_collection(
        self, collection_id: str, recipe_ids: list[str]
    ) -> CookidooCollection | None:
        api = await self.client()
        collection = None
        for recipe_id in recipe_ids:
            collection = await api.remove_recipe_from_custom_collection(collection_id, recipe_id)
        return collection

    @property
    def localization_url(self) -> str | None:
        """Base URL of the Cookidoo site, once connected."""
        return self._api_client.localization.url if self._api_client else None
