# Portions of this file are derived from vitaliemiron/cookidoo-mcp
# (https://github.com/vitaliemiron/cookidoo-mcp, cookidoo_service.py:
# upload_custom_recipe_image, _prepare_custom_recipe_image,
# _get_custom_recipe_image_signature, _upload_custom_recipe_image_to_cloudinary),
# Copyright (c) Alexandre Patelli and Vitalie Miron, licensed under the Apache License,
# Version 2.0 (http://www.apache.org/licenses/LICENSE-2.0). See NOTICE.
#
# Changes from the original: TLS verification stays on (the original disabled it); local
# files are restricted to the COOKIDOO_IMAGE_DIRS allowlist after resolving symlinks, to
# known image types (checked by content, not only extension), to 10 MB and to a pixel cap;
# images are always re-encoded to JPEG without EXIF/GPS or other metadata and downscaled to
# at most 4096 px; the Cookidoo signature and recipe PATCH go through cookidoo-api; the
# Cloudinary request carries no Cookidoo credentials; responses are validated.
"""
Custom recipe images: read a local image safely, re-encode it, and attach it to a recipe.

SECURITY: this is the one tool that reads a local file and sends it to the internet, which
makes it the main exfiltration risk if the model is prompt-injected. Keep every check below.
"""

import os
import re
import time
import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, ClientTimeout, FormData
from cookidoo_api import Cookidoo
from PIL import Image, ImageOps, UnidentifiedImageError

# Cookidoo's signed Cloudinary upload target. Constants, never inputs.
CLOUDINARY_UPLOAD_URL = "https://api-eu.cloudinary.com/v1_1/vorwerk-users-gc/image/upload"
CLOUDINARY_API_KEY = (
    "993585863591145"  # public client key of Cookidoo's web editor  # gitleaks:allow
)
CLOUDINARY_UPLOAD_PRESET = "prod-customer-recipe-signed"
CLOUDINARY_SOURCE = "uw"

IMAGE_DIRS_ENV = "COOKIDOO_IMAGE_DIRS"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 50_000_000  # 50 MP; larger images are refused (decompression bombs)
MAX_SIDE = 4096
MIN_SIDE = 80  # Cloudinary's moderation refuses smaller images (live test, 2026-11-10)
EXTENSIONS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}
if ".heic" in Image.registered_extensions():  # only with a HEIF plugin installed
    EXTENSIONS[".heic"] = "HEIF"

_PUBLIC_ID = re.compile(r"^[A-Za-z0-9_/-]{1,200}$")


class ImageRejected(ValueError):
    """The image path or content is not allowed. The message is safe to show the model."""


@dataclass(frozen=True)
class UploadedImage:
    key: str
    filename: str


def allowed_dirs(value: str | None = None) -> list[Path]:
    """Directories from COOKIDOO_IMAGE_DIRS (os.pathsep-separated), resolved. Empty: disabled."""
    raw = os.getenv(IMAGE_DIRS_ENV, "") if value is None else value
    dirs = []
    for entry in raw.split(os.pathsep):
        if entry.strip():
            path = Path(entry.strip()).expanduser().resolve()
            if path.is_dir():
                dirs.append(path)
    return dirs


def resolve_image_path(image_path: str, dirs: list[Path]) -> Path:
    """The real path of an image inside one of the allowed directories."""
    if not dirs:
        raise ImageRejected(
            f"Image upload is disabled: set {IMAGE_DIRS_ENV} to the folder(s) the server may "
            "read images from (separated by ':' on Linux/macOS, ';' on Windows)."
        )
    candidate = Path(image_path).expanduser()
    if not candidate.is_absolute():
        raise ImageRejected("Give the absolute path of the image file.")
    try:
        path = candidate.resolve(strict=True)  # follows symlinks: checked on the real target
    except (OSError, RuntimeError) as e:
        raise ImageRejected("Image file not found.") from e
    if not any(path.is_relative_to(d) for d in dirs):
        raise ImageRejected(f"Image is outside the folders allowed by {IMAGE_DIRS_ENV}.")
    if not path.is_file():
        raise ImageRejected("Not a regular file.")
    if path.suffix.lower() not in EXTENSIONS:
        raise ImageRejected(f"Unsupported image type. Allowed: {', '.join(sorted(EXTENSIONS))}.")
    return path


def read_limited(path: Path) -> bytes:
    with path.open("rb") as f:
        data = f.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageRejected("Image must be 10 MB or smaller.")
    return data


def reencode_jpeg(data: bytes, expected_format: str) -> bytes:
    """
    Decode the image, then write a fresh JPEG: no EXIF (GPS, camera, dates), no ICC or other
    metadata, orientation applied to the pixels, transparency flattened onto white.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(BytesIO(data), formats=[expected_format]) as source:
                if source.width * source.height > MAX_PIXELS:
                    raise ImageRejected("Image has too many pixels (max. 50 megapixels).")
                image = ImageOps.exif_transpose(source)
                image.load()
        except (
            UnidentifiedImageError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ) as e:
            raise ImageRejected("Not a valid image of the type its extension says.") from e
        except OSError as e:
            raise ImageRejected("The image file is damaged or cannot be read.") from e

    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        image = Image.new("RGB", rgba.size, "white")
        image.paste(rgba, mask=rgba.getchannel("A"))
    else:
        image = image.convert("RGB")
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    if min(image.size) < MIN_SIDE:
        raise ImageRejected(
            f"Image is too small or too narrow: each side must be at least {MIN_SIDE} pixels "
            f"(after scaling to at most {MAX_SIDE})."
        )

    output = BytesIO()
    image.save(output, format="JPEG", quality=90, optimize=True)  # no exif=/icc_profile=
    jpeg = output.getvalue()
    if len(jpeg) > MAX_IMAGE_BYTES:
        raise ImageRejected("Converted image is larger than Cookidoo's 10 MB limit.")
    return jpeg


def prepare_image(image_path: str, dirs: list[Path] | None = None) -> tuple[bytes, str]:
    """Validated, re-encoded JPEG bytes and an upload filename for a local image."""
    path = resolve_image_path(image_path, allowed_dirs() if dirs is None else dirs)
    jpeg = reencode_jpeg(read_limited(path), EXTENSIONS[path.suffix.lower()])
    safe_stem = re.sub(r"[^A-Za-z0-9_-]", "_", path.stem)[:60] or "recipe"
    return jpeg, f"{safe_stem}.jpg"


async def _signature(api: Cookidoo, timestamp: int) -> str:
    """Cookidoo's short-lived signature for one Cloudinary upload."""
    await api._ensure_endpoints()
    url = (
        api.api_endpoint
        / api._path("customer-recipes:recipe-create").format(**api.localization.__dict__)
        / "image"
        / "signature"
    )
    result: Any = await api._request_json(
        "post",
        url,
        "sign recipe image upload",
        json={"source": CLOUDINARY_SOURCE, "timestamp": timestamp},
        headers={"CONTENT-TYPE": "application/json", "ACCEPT": "application/json"},
    )
    signature = result.get("signature") if isinstance(result, dict) else None
    if not isinstance(signature, str) or not re.fullmatch(r"[A-Za-z0-9]{10,128}", signature):
        raise RuntimeError("Cookidoo did not return a valid image upload signature.")
    return signature


async def _upload(
    session: ClientSession, jpeg: bytes, filename: str, timestamp: int, signature: str
) -> str:
    """Send the image to Cloudinary. Only the signed form: no Cookidoo token or cookies."""
    form = FormData()
    form.add_field("file", jpeg, filename=filename, content_type="image/jpeg")
    form.add_field("api_key", CLOUDINARY_API_KEY)
    form.add_field("timestamp", str(timestamp))
    form.add_field("source", CLOUDINARY_SOURCE)
    form.add_field("upload_preset", CLOUDINARY_UPLOAD_PRESET)
    form.add_field("signature", signature)
    async with session.post(
        CLOUDINARY_UPLOAD_URL, data=form, timeout=ClientTimeout(total=60), allow_redirects=False
    ) as response:
        try:
            payload = await response.json(content_type=None)
        except ValueError:
            payload = None
        if response.status > 299:
            raise RuntimeError(
                f"Cookidoo's image storage rejected the upload ({response.status})"
                + _reason(payload)
            )
    if not isinstance(payload, dict):
        raise RuntimeError("Cookidoo's image storage returned an invalid response.")
    public_id, image_format = payload.get("public_id"), payload.get("format")
    if (
        not (isinstance(public_id, str) and _PUBLIC_ID.fullmatch(public_id))
        or image_format != "jpg"
    ):
        raise RuntimeError("Cookidoo's image storage returned no usable image key.")
    return f"{public_id}.{image_format}"


def _reason(payload: object) -> str:
    """Cloudinary's error message, shortened and reduced to plain text, or nothing."""
    error = payload.get("error") if isinstance(payload, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    if not isinstance(message, str):
        return "."
    message = re.sub(r"\s+", " ", message.split(" String to sign")[0])
    message = re.sub(r"[^\w .,:;()'%/-]", "", message).strip()[:200]
    return f": {message}" if message else "."


async def upload_recipe_image(
    api: Cookidoo, session: ClientSession, recipe_id: str, jpeg: bytes, filename: str
) -> UploadedImage:
    """Upload a prepared JPEG and set it as the image of a custom recipe."""
    timestamp = int(time.time())
    signature = await _signature(api, timestamp)
    key = await _upload(session, jpeg, filename, timestamp, signature)
    await api._patch_custom_recipe(
        recipe_id,
        {"image": key, "isImageOwnedByUser": True},  # type: ignore[typeddict-unknown-key]
        "set custom recipe image",
    )
    return UploadedImage(key=key, filename=filename)
