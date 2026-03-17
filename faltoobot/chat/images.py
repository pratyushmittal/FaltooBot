import base64
import mimetypes
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from openai import AsyncOpenAI
from PIL import Image

from faltoobot.store import Session

IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})
MARKDOWN_IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)]+)\)")
MAX_IMAGE_WIDTH = 1600
MAX_IMAGE_HEIGHT = 1200


def as_session_path(source: str, workspace: Path) -> Path | None:
    def existing(path: Path) -> Path | None:
        try:
            return path if path.exists() else None
        except OSError:
            return None

    value = source.strip().strip('"').strip("'")
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme == "file":
        path = Path(unquote(parsed.path))
    elif parsed.scheme:
        return None
    else:
        raw = Path(os.path.expanduser(value))
        path = raw if raw.is_absolute() else workspace / raw
        if existing(path) is None and "\\" in value:
            try:
                parts = shlex.split(value)
            except ValueError:
                parts = []
            if len(parts) == 1:
                raw = Path(os.path.expanduser(parts[0]))
                path = raw if raw.is_absolute() else workspace / raw
    if existing(path) is None:
        return None
    try:
        return path.resolve()
    except OSError:
        return None


def is_image_path(path: Path) -> bool:
    mime_type, _ = mimetypes.guess_type(path.name)
    return path.is_file() and (
        (mime_type or "").startswith("image/") or path.suffix.lower() in IMAGE_EXTENSIONS
    )


def is_image_url(source: str) -> bool:
    value = source.strip()
    if value.startswith("data:image/"):
        return True
    parsed = urlparse(value)
    return (
        parsed.scheme in {"http", "https"} and Path(parsed.path).suffix.lower() in IMAGE_EXTENSIONS
    )


def image_markdown(path: Path, alt: str | None = None) -> str:
    return f"![{alt or path.name}]({path.as_uri()})"


def paste_image_text(text: str, workspace: Path) -> str:
    lines = text.splitlines()
    if not lines:
        return text
    return "\n".join(
        f"![image]({line.strip()})"
        if is_image_url(line)
        else image_markdown(path)
        if (path := as_session_path(line, workspace)) and is_image_path(path)
        else line
        for line in lines
    )


def image_label(source: str, alt: str, workspace: Path) -> str:
    if alt.strip():
        return alt.strip()
    if path := as_session_path(source, workspace):
        return path.name
    parsed = urlparse(source.strip())
    name = Path(unquote(parsed.path)).name
    return name or "image"


def display_prompt(prompt: str, workspace: Path) -> str:
    text = MARKDOWN_IMAGE_RE.sub(
        lambda match: f"[image: {image_label(match.group('src'), match.group('alt'), workspace)}]",
        prompt,
    ).strip()
    return text or "[image]"


def clipboard_image_bytes() -> bytes | None:
    if sys.platform != "darwin":
        return None
    script = """
import AppKit
let pasteboard = NSPasteboard.general
if let image = NSImage(pasteboard: pasteboard),
   let tiff = image.tiffRepresentation,
   let bitmap = NSBitmapImageRep(data: tiff),
   let png = bitmap.representation(using: .png, properties: [:]) {
    print(png.base64EncodedString())
}
""".strip()
    try:
        result = subprocess.run(
            ["swift", "-"],
            input=script,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    data = result.stdout.strip()
    return base64.b64decode(data) if result.returncode == 0 and data else None


def save_clipboard_image(session: Session) -> Path | None:
    if not (data := clipboard_image_bytes()):
        return None
    path = (
        session.root
        / "attachments"
        / f"clipboard-{datetime.now().astimezone().strftime('%Y%m%d-%H%M%S-%f')}.png"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def fitted_image_size(width: int, height: int) -> tuple[int, int]:
    scale = min(MAX_IMAGE_WIDTH / width, MAX_IMAGE_HEIGHT / height, 1)
    return max(1, int(width * scale)), max(1, int(height * scale))


def resized_image_upload(path: Path) -> BytesIO | None:
    with Image.open(path) as image:
        width, height = image.size
        target = fitted_image_size(width, height)
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


async def input_image_part(client: AsyncOpenAI, workspace: Path, source: str) -> dict[str, Any]:
    value = source.strip()
    if is_image_url(value):
        return {"type": "input_image", "image_url": value, "detail": "auto"}
    path = as_session_path(value, workspace)
    if path is None or not is_image_path(path):
        raise ValueError(f"Image not found: {source}")
    if upload := resized_image_upload(path):
        uploaded = await client.files.create(file=upload, purpose="vision")
    else:
        with path.open("rb") as handle:
            uploaded = await client.files.create(file=handle, purpose="vision")
    return {"type": "input_image", "file_id": uploaded.id, "detail": "auto"}


async def prompt_message_item(
    client: AsyncOpenAI,
    workspace: Path,
    prompt: str,
) -> tuple[str, dict[str, Any] | None]:
    if not MARKDOWN_IMAGE_RE.search(prompt):
        return prompt, None
    content: list[dict[str, Any]] = []
    cursor = 0
    for match in MARKDOWN_IMAGE_RE.finditer(prompt):
        prefix = prompt[cursor : match.start()]
        if prefix:
            content.append({"type": "input_text", "text": prefix})
        content.append(await input_image_part(client, workspace, match.group("src")))
        cursor = match.end()
    suffix = prompt[cursor:]
    if suffix:
        content.append({"type": "input_text", "text": suffix})
    return display_prompt(prompt, workspace), {
        "type": "message",
        "role": "user",
        "content": [
            part
            for part in content
            if part.get("type") == "input_image" or str(part.get("text") or "")
        ],
    }
