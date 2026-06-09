import base64
import mimetypes
from io import BytesIO
from pathlib import Path
from typing import TypeAlias

from openai import AsyncOpenAI
from openai.types.responses import ResponseInputImage
from PIL import Image

MAX_IMAGE_WIDTH = 1600
MAX_IMAGE_HEIGHT = 1200
MAX_INLINE_IMAGE_BYTES = 4 * 1024 * 1024
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
        resized = image.resize(target, Image.Resampling.LANCZOS)
        buffer = BytesIO()
        format_name = "JPEG" if image.format in {"JPEG", "JPG"} else "PNG"
        suffix = ".jpg" if format_name == "JPEG" else ".png"
        resized.save(buffer, format=format_name)
    buffer.seek(0)
    buffer.name = f"{path.stem}-{target[0]}x{target[1]}{suffix}"
    return buffer


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
