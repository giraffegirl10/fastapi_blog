import colorsys
import hashlib
import uuid
from functools import lru_cache
from io import BytesIO
from pathlib import Path

import boto3
from PIL import Image, ImageDraw, ImageFont, ImageOps
from starlette.concurrency import run_in_threadpool

from config import settings

AVATAR_FONT = Path(__file__).parent / "static" / "fonts" / "Sniglet-ExtraBold.ttf"


def _get_s3_client():
    return boto3.client(
        "s3",
        region_name=settings.s3_region,
        aws_access_key_id=(
            settings.s3_access_key_id.get_secret_value()
            if settings.s3_access_key_id
            else None
        ),
        aws_secret_access_key=(
            settings.s3_secret_access_key.get_secret_value()
            if settings.s3_secret_access_key
            else None
        ),
        endpoint_url=settings.s3_endpoint_url,
    )


def process_profile_image(content: bytes) -> tuple[bytes, str]:
    with Image.open(BytesIO(content)) as original:
        img = ImageOps.exif_transpose(original)

        img = ImageOps.fit(img, (300, 300), method=Image.Resampling.LANCZOS)

        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")

        filename = f"{uuid.uuid4().hex}.jpg"

        output = BytesIO()
        img.save(output, "JPEG", quality=85, optimize=True)
        output.seek(0)

    return output.read(), filename


def _upload_to_s3(file_bytes: bytes, key: str) -> None:
    s3 = _get_s3_client()
    s3.upload_fileobj(
        BytesIO(file_bytes),
        settings.s3_bucket_name,
        key,
        ExtraArgs={"ContentType": "image/jpeg"},
    )


def _delete_from_s3(key: str) -> None:
    s3 = _get_s3_client()
    s3.delete_object(Bucket=settings.s3_bucket_name, Key=key)


async def upload_profile_image(file_bytes: bytes, filename: str) -> None:
    key = f"profile_pics/{filename}"
    await run_in_threadpool(_upload_to_s3, file_bytes, key)


async def delete_profile_image(filename: str | None) -> None:
    if filename is None:
        return
    key = f"profile_pics/{filename}"
    await run_in_threadpool(_delete_from_s3, key)

def username_initials(username: str) -> str:
    """"QuietFalcon42" -> "QF", "jane_doe" -> "JD", "heather" -> "H"."""
    # A word starts at a letter after a non-letter, or a capital after lowercase
    starts = [
        char
        for prev, char in zip(" " + username, username, strict=False)
        if char.isalpha() and (not prev.isalpha() or (char.isupper() and prev.islower()))
    ]
    initials = "".join(starts[:2]).upper()
    return initials or username[:1].upper() or "?"


@lru_cache(maxsize=512)
def initials_avatar(username: str) -> bytes:
    """Draw pastel bubble-letter initials on a transparent background.

    The avatar circles get the page's dotted background in CSS, so it shows
    through and follows the light/dark theme. The color comes from a hash of
    the username, so each user keeps the same one.
    """
    size = 300
    hue = int(hashlib.md5(username.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    color = tuple(round(c * 255) for c in colorsys.hls_to_rgb(hue, 0.76, 0.7))
    # Dark outline (the site's --ink) so light colors stay readable on the
    # light page background too
    ink = (26, 26, 46)

    # Draw the letters as masks so their edges blend smoothly into whatever
    # is behind the image: one for the fill, a wider one for fill + outline
    initials = username_initials(username)
    stroke = 7
    # As big as possible while wide pairs like "MW" still fit inside the circle
    font = ImageFont.truetype(AVATAR_FONT, 150)
    left, _, right, _ = font.getbbox(initials, stroke_width=stroke)
    if right - left > 220:
        font = font.font_variant(size=int(150 * 220 / (right - left)))

    def text_mask(stroke_width: int) -> Image.Image:
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).text(
            (size / 2, size / 2), initials, fill=255, font=font, anchor="mm",
            stroke_width=stroke_width, stroke_fill=255,
        )
        return mask

    img = Image.composite(
        Image.new("RGB", (size, size), color),
        Image.new("RGB", (size, size), ink),
        text_mask(0),
    )
    img.putalpha(text_mask(stroke))

    output = BytesIO()
    img.save(output, "PNG")
    return output.getvalue()
