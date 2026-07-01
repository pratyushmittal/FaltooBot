import base64
import binascii
import mimetypes
from io import BytesIO
from pathlib import Path
from typing import TypeAlias

from openai import AsyncOpenAI
from openai.types.responses import ResponseInputImage
from PIL import Image

from faltoobot.gpt_utils import MessageHistory

MAX_IMAGE_WIDTH = 1600
MAX_IMAGE_HEIGHT = 1200
MAX_INLINE_IMAGE_BYTES = 4 * 1024 * 1024
INLINE_HISTORY_RESIZE_STEP = 0.7
_SUPPORTED_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
_SUPPORTED_IMAGE_MIME_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp"}
)

Attachment: TypeAlias = str | Path


def _attachment_path(source: Attachment, workspace: Path) -> Path:
    path = (
        source if isinstance(source, Path) else Path(str(source).strip()).expanduser()
    )
    return path if path.is_absolute() else workspace / path


def _is_supported_image_path(path: Path) -> bool:
    mime_type, _ = mimetypes.guess_type(path.name)
    return path.is_file() and (
        mime_type in _SUPPORTED_IMAGE_MIME_TYPES
        or path.suffix.lower() in _SUPPORTED_IMAGE_EXTENSIONS
    )


def _ensure_inline_image_supported(path: Path, source: Attachment) -> None:
    """Fail inside load_image before unsupported inline inputs enter chat history."""
    if _is_supported_image_path(path):
        return
    if path.is_file() and (mimetypes.guess_type(path.name)[0] or "").startswith(
        "image/"
    ):
        # comment: image-looking files need a clearer format error.
        raise ValueError(
            f"Unsupported image format for OpenAI: {source}. "
            "Supported formats: jpeg, png, gif, webp."
        )
    raise ValueError(f"Unsupported attachment: {source}")


def _ensure_inline_image_size(data: bytes, source: Attachment) -> None:
    if len(data) <= MAX_INLINE_IMAGE_BYTES:
        # comment: small inline images are safe for OAuth requests.
        return
    max_mib = MAX_INLINE_IMAGE_BYTES // (1024 * 1024)
    raise ValueError(
        f"Image is too large to send inline: {source}. "
        f"Maximum inline image size is {max_mib} MiB."
    )


def _fitted_image_size(width: int, height: int) -> tuple[int, int]:
    scale = min(MAX_IMAGE_WIDTH / width, MAX_IMAGE_HEIGHT / height, 1)
    return max(1, int(width * scale)), max(1, int(height * scale))


def _resized_image_bytes(
    image: Image.Image,
    size: tuple[int, int],
    *,
    format_name: str,
    quality: int | None = None,
) -> bytes:
    resized = (
        image.resize(size, Image.Resampling.LANCZOS) if image.size != size else image
    )
    if format_name == "JPEG" and (
        resized.mode in {"RGBA", "LA"} or "transparency" in resized.info
    ):
        # comment: JPEG cannot store alpha; flatten transparent images before saving.
        background = Image.new("RGB", resized.size, "white")
        alpha = resized.convert("RGBA").getchannel("A")
        background.paste(resized.convert("RGBA"), mask=alpha)
        resized = background
    if format_name == "JPEG" and resized.mode != "RGB":
        # comment: JPEG needs RGB/L output; RGB is safest for screenshots and photos.
        resized = resized.convert("RGB")

    buffer = BytesIO()
    kwargs = {"quality": quality, "optimize": True} if quality is not None else {}
    resized.save(buffer, format=format_name, **kwargs)
    return buffer.getvalue()


def _resized_image_upload(path: Path) -> BytesIO | None:
    try:
        image_context = Image.open(path)
    except OSError:
        # comment: future/unsupported formats should pass through to OpenAI upload.
        return None

    with image_context as image:
        width, height = image.size
        target = _fitted_image_size(width, height)
        if target == (width, height):
            return None
        format_name = "JPEG" if image.format in {"JPEG", "JPG"} else "PNG"
        suffix = ".jpg" if format_name == "JPEG" else ".png"
        data = _resized_image_bytes(image, target, format_name=format_name)

    buffer = BytesIO(data)
    buffer.seek(0)
    buffer.name = f"{path.stem}-{target[0]}x{target[1]}{suffix}"
    return buffer


def _extract_base64_image(base64_image_str: str) -> bytes | None:
    header, separator, encoded = base64_image_str.partition(",")
    if separator != "," or not header.startswith("data:image/"):
        return None
    if ";base64" not in header:
        return None
    try:
        return base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        return None


def _shrink_until_within_limit(base64_image_str: str) -> str | None:
    original = _extract_base64_image(base64_image_str)
    if original is None:
        return None

    try:
        image_context = Image.open(BytesIO(original))
    except OSError:
        return None

    best = original
    with image_context as image:
        image.load()
        width, height = _fitted_image_size(*image.size)
        while width > 1 and height > 1:
            data = _resized_image_bytes(
                image, (width, height), format_name="JPEG", quality=85
            )
            if len(data) < len(best):
                best = data
            if len(data) <= MAX_INLINE_IMAGE_BYTES:
                break
            width = max(1, int(width * INLINE_HISTORY_RESIZE_STEP))
            height = max(1, int(height * INLINE_HISTORY_RESIZE_STEP))

    if len(best) >= len(original):
        return None
    encoded = base64.b64encode(best).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def resize_inline_images_in_history(history: MessageHistory) -> int:
    """Shrink inline images saved in load_image tool output history."""
    changed = 0
    for item in history:
        if item.get("type") == "function_call_output":
            output = item.get("output")
            if not isinstance(output, list):
                # comment: text tool outputs are strings and cannot carry inline images.
                continue

            for part in output:
                # comment: old/corrupt local histories may have non-dict parts.
                if isinstance(part, dict):
                    image_url = part.get("image_url")
                    if part.get("type") == "input_image" and isinstance(image_url, str):
                        replacement = _shrink_until_within_limit(image_url)
                        if replacement is not None:
                            part["image_url"] = replacement
                            changed += 1
    return changed


async def upload_attachment(
    client: AsyncOpenAI, workspace: Path, source: Attachment
) -> ResponseInputImage:
    path = _attachment_path(source, workspace)
    if not path.exists():
        raise ValueError(f"Attachment not found: {source}")
    if upload := _resized_image_upload(path):
        uploaded = await client.files.create(file=upload, purpose="vision")
    else:
        with path.open("rb") as handle:
            uploaded = await client.files.create(file=handle, purpose="vision")
    return ResponseInputImage(type="input_image", file_id=uploaded.id, detail="auto")


def inline_image_item(workspace: Path, source: Attachment) -> ResponseInputImage:
    path = _attachment_path(source, workspace)
    if not path.exists():
        raise ValueError(f"Attachment not found: {source}")
    # comment: inline images return local response items; fail here so tool calls get the error.
    _ensure_inline_image_supported(path, source)
    upload = _resized_image_upload(path)
    if upload is None:
        data = path.read_bytes()
        mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    else:
        data = upload.getvalue()
        mime_type = mimetypes.guess_type(upload.name)[0] or "image/png"
    _ensure_inline_image_size(data, source)
    encoded = base64.b64encode(data).decode("ascii")
    return ResponseInputImage(
        type="input_image",
        image_url=f"data:{mime_type};base64,{encoded}",
        detail="auto",
    )
