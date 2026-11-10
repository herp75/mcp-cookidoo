"""Copy to custom recipe, move in meal plan, and the hardened recipe image upload."""

import os
from datetime import date
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp.exceptions import ToolError
from PIL import Image

import cookidoo_service
import recipe_images
from cookidoo_service import CookidooService, CookidooSettings
from recipe_images import ImageRejected, prepare_image, reencode_jpeg, resolve_image_path
from tests.conftest import SECRET, request_error, run

CUSTOM = "01TESTRECIPE00000000000001"


def write_image(path, fmt="JPEG", size=(64, 48), mode="RGB", **save_args):
    Image.new(mode, size, "red").save(path, format=fmt, **save_args)
    return path


def gps_exif() -> Image.Exif:
    exif = Image.Exif()
    exif[0x010F] = "TestCam Maker"  # Make
    exif[0x0112] = 6  # Orientation: rotate 90° clockwise to display
    exif.get_ifd(0x8825).update({1: "N", 2: (48.0, 8.0, 30.0), 3: "E", 4: (11.0, 34.0, 10.0)})
    return exif


@pytest.fixture
def image_dir(tmp_path, monkeypatch):
    allowed = tmp_path / "photos"
    allowed.mkdir()
    monkeypatch.setenv("COOKIDOO_IMAGE_DIRS", str(allowed))
    return allowed


# --- Path allowlist ------------------------------------------------------------------------


def test_image_inside_allowed_dir(image_dir):
    path = write_image(image_dir / "soup.jpg")
    jpeg, filename = prepare_image(str(path))
    assert filename == "soup.jpg" and jpeg.startswith(b"\xff\xd8")


def test_upload_disabled_without_allowlist(tmp_path, monkeypatch):
    monkeypatch.delenv("COOKIDOO_IMAGE_DIRS", raising=False)
    path = write_image(tmp_path / "soup.jpg")
    with pytest.raises(ImageRejected, match="disabled: set COOKIDOO_IMAGE_DIRS"):
        prepare_image(str(path))


def test_allowlist_ignores_missing_dirs(tmp_path):
    assert recipe_images.allowed_dirs(f"{tmp_path / 'nope'}{os.pathsep}{os.pathsep}") == []


def test_several_allowed_dirs(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    path = write_image(b / "x.png", "PNG")
    assert resolve_image_path(str(path), recipe_images.allowed_dirs(f"{a}{os.pathsep}{b}"))


@pytest.mark.parametrize(
    "make",
    [
        lambda d, t: write_image(t / "outside.jpg"),  # outside the allowlist
        lambda d, t: str(d / ".." / "outside.jpg"),  # ../ escape
        lambda d, t: os.path.relpath(write_image(d / "rel.jpg")),  # relative path
        lambda d, t: str(d / "missing.jpg"),
        lambda d, t: d,  # a directory
        lambda d, t: write_image(d / "photo.gif", "GIF"),  # type not allowed
    ],
)
def test_rejected_paths(image_dir, tmp_path, make):
    write_image(tmp_path / "outside.jpg")
    with pytest.raises(ImageRejected):
        prepare_image(str(make(image_dir, tmp_path)))


def test_symlink_escaping_the_allowlist_is_rejected(image_dir, tmp_path):
    secret = write_image(tmp_path / "private.jpg")
    (image_dir / "innocent.jpg").symlink_to(secret)
    with pytest.raises(ImageRejected, match="outside the folders"):
        prepare_image(str(image_dir / "innocent.jpg"))


def test_symlink_inside_the_allowlist_is_fine(image_dir):
    real = write_image(image_dir / "real.jpg")
    (image_dir / "link.jpg").symlink_to(real)
    assert prepare_image(str(image_dir / "link.jpg"))[1] == "real.jpg"


def test_non_image_with_image_extension_is_rejected(image_dir):
    fake = image_dir / "notes.jpg"
    fake.write_text("COOKIDOO_PASSWORD=hunter2\n")
    with pytest.raises(ImageRejected, match="Not a valid image"):
        prepare_image(str(fake))


def test_extension_must_match_content(image_dir):
    png = write_image(image_dir / "really_png.jpg", "PNG")
    with pytest.raises(ImageRejected, match="Not a valid image"):
        prepare_image(str(png))


def test_oversized_file_is_rejected(image_dir, monkeypatch):
    path = write_image(image_dir / "big.jpg")
    monkeypatch.setattr(recipe_images, "MAX_IMAGE_BYTES", path.stat().st_size - 1)
    with pytest.raises(ImageRejected, match="10 MB"):
        prepare_image(str(path))


def test_too_many_pixels_is_rejected(image_dir, monkeypatch):
    path = write_image(image_dir / "huge.png", "PNG", size=(200, 200))
    monkeypatch.setattr(recipe_images, "MAX_PIXELS", 100 * 100)
    with pytest.raises(ImageRejected, match="too many pixels"):
        prepare_image(str(path))


def test_decompression_bomb_is_rejected(image_dir, monkeypatch):
    path = write_image(image_dir / "bomb.png", "PNG", size=(300, 300))
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)
    with pytest.raises(ImageRejected):
        prepare_image(str(path))


def test_filename_is_sanitized(image_dir):
    path = write_image(image_dir / "Tomaten suppe; rm -rf.jpg")
    assert prepare_image(str(path))[1] == "Tomaten_suppe__rm_-rf.jpg"


# --- Re-encoding ---------------------------------------------------------------------------


def test_exif_and_gps_are_removed_and_orientation_applied(image_dir):
    path = write_image(image_dir / "gps.jpg", size=(60, 40), exif=gps_exif())
    with Image.open(path) as original:
        assert original.getexif().get_ifd(0x8825)  # the fixture really has GPS data
    jpeg, _ = prepare_image(str(path))
    with Image.open(BytesIO(jpeg)) as result:
        assert result.format == "JPEG"
        assert not result.getexif()
        assert "exif" not in result.info and "icc_profile" not in result.info
        assert result.size == (40, 60)  # rotated by the orientation tag, then dropped
    assert b"TestCam" not in jpeg and b"Exif" not in jpeg


@pytest.mark.parametrize("fmt", ["PNG", "WEBP"])
def test_transparent_images_are_flattened_to_jpeg(fmt):
    buffer = BytesIO()
    Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(buffer, format=fmt)
    jpeg = reencode_jpeg(buffer.getvalue(), fmt)
    with Image.open(BytesIO(jpeg)) as result:
        assert result.mode == "RGB" and result.getpixel((5, 5)) == (255, 255, 255)


def test_large_images_are_downscaled(monkeypatch):
    monkeypatch.setattr(recipe_images, "MAX_SIDE", 100)
    buffer = BytesIO()
    Image.new("RGB", (400, 200)).save(buffer, format="PNG")
    with Image.open(BytesIO(reencode_jpeg(buffer.getvalue(), "PNG"))) as result:
        assert result.size == (100, 50)


# --- Upload flow -----------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status, payload):
        self.status, self._payload = status, payload

    async def json(self, content_type=None):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, status=200, payload=None):
        self.requests = []
        self.response = FakeResponse(
            status, payload or {"public_id": "prod/customer-recipe/abc123", "format": "jpg"}
        )

    def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return self.response

    async def close(self):
        pass


@pytest.fixture
def upload(fake, monkeypatch, image_dir):
    """The fake account with a fake HTTP session for Cloudinary, and an allowed image."""
    import server

    session = FakeSession()
    monkeypatch.setattr(server._cookidoo_service, "_session", session)
    return session, write_image(image_dir / "soup.jpg", exif=gps_exif())


async def test_upload_flow(fake, upload):
    session, path = upload
    text = await run("upload_custom_recipe_image", recipe_id=CUSTOM, image_path=str(path))
    assert text == f"Image set for custom recipe {CUSTOM} (uploaded as soup.jpg)."

    # 1. Signature from Cookidoo, through cookidoo-api (TLS, Cookidoo auth)
    ((method, url, kwargs),) = fake.called("_request_json")
    assert method == "post"
    assert url == "https://cookidoo.example/created-recipes/en-GB/image/signature"
    assert kwargs["json"]["source"] == "uw"

    # 2. Upload to the fixed Cloudinary URL, without any Cookidoo credentials
    ((cloud_url, request),) = session.requests
    assert cloud_url == recipe_images.CLOUDINARY_UPLOAD_URL
    assert "headers" not in request and "cookies" not in request
    assert request["allow_redirects"] is False
    fields = {f[0]["name"]: f[2] for f in request["data"]._fields}
    assert fields["signature"] == fake.signature_response["signature"]
    assert fields["upload_preset"] == "prod-customer-recipe-signed"
    assert str(kwargs["json"]["timestamp"]) == fields["timestamp"]
    assert fields["file"].startswith(b"\xff\xd8") and b"TestCam" not in fields["file"]

    # 3. The recipe points to the uploaded image
    assert fake.called("_patch_custom_recipe") == [
        (CUSTOM, {"image": "prod/customer-recipe/abc123.jpg", "isImageOwnedByUser": True})
    ]


async def test_rejected_image_makes_no_network_call(fake, upload, tmp_path):
    session, _ = upload
    outside = write_image(tmp_path / "elsewhere.jpg")
    with pytest.raises(ToolError, match="Image not uploaded: .*outside the folders"):
        await run("upload_custom_recipe_image", recipe_id=CUSTOM, image_path=str(outside))
    assert fake.calls == [] and session.requests == []


async def test_upload_validates_recipe_id(fake, upload):
    _, path = upload
    with pytest.raises(ToolError, match="Invalid recipe ID"):
        await run("upload_custom_recipe_image", recipe_id="../x", image_path=str(path))


@pytest.mark.parametrize(
    "signature", [None, {}, {"signature": ""}, {"signature": "bad sig!"}, ["x"]]
)
async def test_invalid_signature_stops_before_upload(fake, upload, signature):
    session, path = upload
    fake.signature_response = signature
    with pytest.raises(ToolError, match="Image upload failed: .*signature"):
        await run("upload_custom_recipe_image", recipe_id=CUSTOM, image_path=str(path))
    assert session.requests == [] and fake.called("_patch_custom_recipe") == []


@pytest.mark.parametrize(
    ("status", "payload", "message"),
    [
        (400, {"error": {"message": "Invalid Signature"}}, "rejected the upload \\(400\\)"),
        (200, {"public_id": "x", "format": "png"}, "no usable image key"),
        (200, {"public_id": "../../evil", "format": "jpg"}, "no usable image key"),
        (200, {"format": "jpg"}, "no usable image key"),
    ],
)
async def test_bad_cloudinary_responses(fake, upload, status, payload, message):
    session, path = upload
    session.response = FakeResponse(status, payload)
    with pytest.raises(ToolError, match=message):
        await run("upload_custom_recipe_image", recipe_id=CUSTOM, image_path=str(path))
    assert fake.called("_patch_custom_recipe") == []


async def test_patch_failure_is_reported_without_secrets(fake, upload):
    _, path = upload
    fake.failures["_patch_custom_recipe"] = request_error("(403)")
    with pytest.raises(ToolError) as error:
        await run("upload_custom_recipe_image", recipe_id=CUSTOM, image_path=str(path))
    assert str(error.value).startswith("Image upload failed") and SECRET not in str(error.value)


async def test_cookidoo_session_keeps_tls_verification(monkeypatch):
    """login() creates the aiohttp session with default (verified) TLS, which the upload uses."""
    created = []

    class RecordingSession:
        def __init__(self, *args, **kwargs):
            created.append((args, kwargs))

        async def close(self):
            pass

    client = MagicMock(login=AsyncMock())
    monkeypatch.setattr(cookidoo_service, "ClientSession", RecordingSession)
    monkeypatch.setattr(cookidoo_service, "Cookidoo", lambda **kw: client)
    monkeypatch.setattr(
        cookidoo_service, "get_localization_options", AsyncMock(return_value=[MagicMock()])
    )
    svc = CookidooService(CookidooSettings(email="a@b.c", password="x"))
    await svc.login()
    assert created == [((), {})]  # no connector, no ssl argument


# --- Copy to custom recipe -------------------------------------------------------------------


async def test_copy_recipe_to_custom_uses_original_servings(fake):
    text = await run("copy_recipe_to_custom", recipe_id="r100001")
    assert "Recipe copied as 'Test Tomato Soup (copy)' (4 servings)." in text
    assert "Custom recipe ID: 01TESTCOPY0000000000000001" in text
    assert fake.called("add_custom_recipe_from") == [("r100001", 4)]


async def test_copy_recipe_to_custom_with_servings(fake):
    await run("copy_recipe_to_custom", recipe_id="r100001", servings=6)
    assert fake.called("get_recipe_details") == []
    assert fake.called("add_custom_recipe_from") == [("r100001", 6)]


async def test_copy_recipe_to_custom_validation_and_error(fake):
    for args in ({"recipe_id": "a/b"}, {"recipe_id": "r1", "servings": 0}):
        with pytest.raises(ToolError):
            await run("copy_recipe_to_custom", **args)
    assert fake.calls == []
    fake.failures["add_custom_recipe_from"] = request_error("(404)")
    with pytest.raises(ToolError) as error:
        await run("copy_recipe_to_custom", recipe_id="r1", servings=2)
    assert str(error.value).startswith("Copy failed") and SECRET not in str(error.value)


# --- Move in meal plan -----------------------------------------------------------------------

MONDAY, TUESDAY, FRIDAY = date(2026, 11, 9), date(2026, 11, 10), date(2026, 11, 13)


async def move(recipe_id="r100001", from_day="2026-11-09", to_day="2026-11-13") -> str:
    return await run("move_in_meal_plan", recipe_id=recipe_id, from_day=from_day, to_day=to_day)


async def test_move_recipe_adds_before_removing(fake):
    text = await run(
        "move_in_meal_plan", recipe_id="r100001", from_day="2026-11-09", to_day="2026-11-13"
    )
    assert text == "Moved recipe r100001 from 2026-11-09 to 2026-11-13."
    names = [name for name, _ in fake.calls if "calendar" in name]
    assert names == [
        "get_recipes_in_calendar_week",
        "add_recipes_to_calendar",
        "remove_recipe_from_calendar",
    ]
    assert fake.called("add_recipes_to_calendar") == [(FRIDAY, ["r100001"])]
    assert fake.called("remove_recipe_from_calendar") == [(MONDAY, "r100001")]


async def test_move_custom_recipe_uses_custom_endpoints(fake):
    text = await run(
        "move_in_meal_plan", recipe_id=CUSTOM, from_day="2026-11-10", to_day="2026-11-13"
    )
    assert text.startswith("Moved custom recipe")
    assert fake.called("add_custom_recipes_to_calendar") == [(FRIDAY, [CUSTOM])]
    assert fake.called("remove_custom_recipe_from_calendar") == [(TUESDAY, CUSTOM)]
    assert fake.called("add_recipes_to_calendar") == []


async def test_move_unresolved_custom_recipe(fake):
    missing = "01TESTRECIPEMISSING00000001"  # only in customer_recipe_ids
    await move(missing, "2026-11-10")
    assert fake.called("remove_custom_recipe_from_calendar") == [(TUESDAY, missing)]


@pytest.mark.parametrize(
    ("recipe_id", "from_day"),
    [("r100001", "2026-11-10"), ("r999", "2026-11-09"), ("r1", "2026-11-12")],
)
async def test_move_requires_recipe_on_from_day(fake, recipe_id, from_day):
    with pytest.raises(ToolError, match=f"{recipe_id} is not planned on {from_day}"):
        await move(recipe_id, from_day)
    assert fake.called("add_recipes_to_calendar") == []


async def test_move_to_same_day_does_nothing(fake):
    text = await run(
        "move_in_meal_plan", recipe_id="r100001", from_day="2026-11-09", to_day="2026-11-09"
    )
    assert text.startswith("Nothing to do") and fake.calls == []


async def test_move_rolls_back_when_removal_fails(fake):
    calls = []

    async def remove(day, recipe_id):
        calls.append(day)
        if day == MONDAY:
            raise request_error("(500)")

    fake.remove_recipe_from_calendar = remove
    with pytest.raises(ToolError) as error:
        await run(
            "move_in_meal_plan", recipe_id="r100001", from_day="2026-11-09", to_day="2026-11-13"
        )
    message = str(error.value)
    assert "nothing changed" in message and "still planned on 2026-11-09 only" in message
    assert SECRET not in message
    assert calls == [MONDAY, FRIDAY]  # removal failed, then the addition was undone


async def test_move_reports_failed_rollback(fake):
    async def remove(day, recipe_id):
        raise request_error("(500)")

    fake.remove_recipe_from_calendar = remove
    with pytest.raises(ToolError) as error:
        await run(
            "move_in_meal_plan", recipe_id="r100001", from_day="2026-11-09", to_day="2026-11-13"
        )
    message = str(error.value)
    assert "could not be rolled back" in message and "both 2026-11-09 and 2026-11-13" in message
    assert SECRET not in message and "abc.def.ghi" not in message


async def test_move_failing_add_changes_nothing(fake):
    fake.failures["add_recipes_to_calendar"] = request_error("(500)")
    with pytest.raises(ToolError, match="Move failed, nothing changed"):
        await run(
            "move_in_meal_plan", recipe_id="r100001", from_day="2026-11-09", to_day="2026-11-13"
        )
    assert fake.called("remove_recipe_from_calendar") == []
