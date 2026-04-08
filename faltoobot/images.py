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
_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})

Attachment: TypeAlias = str | Path


def _attachment_path(source: Attachment, workspace: Path) -> Path:
    path = (
        source if isinstance(source, Path) else Path(str(source).strip()).expanduser()
    )
    return path if path.is_absolute() else workspace / path


def _is_image_path(path: Path) -> bool:
    mime_type, _ = mimetypes.guess_type(path.name)
    return path.is_file() and (
        (mime_type or "").startswith("image/")
        or path.suffix.lower() in _IMAGE_EXTENSIONS
    )


def _fitted_image_size(width: int, height: int) -> tuple[int, int]:
    scale = min(MAX_IMAGE_WIDTH / width, MAX_IMAGE_HEIGHT / height, 1)
    return max(1, int(width * scale)), max(1, int(height * scale))


def _resized_image_upload(path: Path) -> BytesIO | None:
    with Image.open(path) as image:
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
    if not _is_image_path(path):
        raise ValueError(f"Unsupported attachment: {source}")
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
    if not _is_image_path(path):
        raise ValueError(f"Unsupported attachment: {source}")
    upload = _resized_image_upload(path)
    if upload is None:
        data = path.read_bytes()
        mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    else:
        data = upload.getvalue()
        mime_type = mimetypes.guess_type(upload.name)[0] or "image/png"
    encoded = base64.b64encode(data).decode("ascii")
    return ResponseInputImage(
        type="input_image",
        image_url=f"data:{mime_type};base64,{encoded}",
        detail="auto",
    )
