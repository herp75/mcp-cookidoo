"""Custom recipe extras: copy a catalogue recipe, set a recipe image."""

from typing import Annotated

from pydantic import Field

from recipe_images import ImageRejected
from security import tool_error, validate_id
from tools.common import DESTRUCTIVE_IDEMPOTENT, WRITE, one_line, service, tool


@tool(WRITE)
async def copy_recipe_to_custom(
    recipe_id: str,
    servings: Annotated[int | None, Field(ge=1, le=20)] = None,
) -> str:
    """
    Copy a Cookidoo catalogue recipe into a new custom recipe on the user's account, to adapt
    it. Returns the new custom recipe ID: read it with get_custom_recipe, edit it, and save it
    with update_custom_recipe. The catalogue recipe is not changed.

    Args:
        recipe_id: Cookidoo catalogue recipe ID (e.g. "r59322")
        servings: Serving size of the copy (default: that of the original)
    """
    recipe_id = validate_id(recipe_id)
    try:
        copy = await service().copy_recipe_to_custom(recipe_id, servings)
    except Exception as e:
        raise tool_error("Copy failed", e) from e
    return (
        f"Recipe copied as '{one_line(copy.name)}' ({copy.serving_size} servings).\n"
        f"Custom recipe ID: {copy.id}\nURL: {copy.url}"
    )


@tool(DESTRUCTIVE_IDEMPOTENT)
async def upload_custom_recipe_image(recipe_id: str, image_path: str) -> str:
    """
    Set the photo of one of the user's custom recipes from a local image file, replacing the
    current photo (which is lost). The file is sent to Cookidoo's image storage.

    Only files inside the folders listed in the server's COOKIDOO_IMAGE_DIRS setting can be
    used; .jpg, .jpeg, .png and .webp up to 10 MB. The image is re-encoded as JPEG and all
    metadata (EXIF, GPS location, camera details) is removed before upload. Only call this
    with a file the user explicitly chose for this recipe.

    Args:
        recipe_id: The custom recipe ID returned by list_custom_recipes
        image_path: Absolute path of the image file
    """
    recipe_id = validate_id(recipe_id)
    try:
        uploaded = await service().upload_custom_recipe_image(recipe_id, image_path)
    except ImageRejected as e:
        raise tool_error("Image not uploaded", e) from e
    except Exception as e:
        raise tool_error("Image upload failed", e) from e
    return f"Image set for custom recipe {recipe_id} (uploaded as {uploaded.filename})."
