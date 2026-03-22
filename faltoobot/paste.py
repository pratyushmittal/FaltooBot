import base64
import mimetypes
import os
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from faltoobot import sessions

IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})


def _attachment_path(session: sessions.Session) -> Path:
    return (
        sessions.get_messages_path(session).parent
        / "attachments"
        / f"clipboard-{datetime.now().astimezone().strftime('%Y%m%d-%H%M%S-%f')}.png"
    )


def _workspace_path(value: str, workspace: Path) -> Path:
    raw = Path(os.path.expanduser(value))
    return raw if raw.is_absolute() else workspace / raw


def _shell_escaped_path(value: str, workspace: Path) -> Path | None:
    if "\\" not in value:
        return None
    try:
        parts = shlex.split(value)
    except ValueError:
        return None
    if len(parts) != 1:
        return None
    return _workspace_path(parts[0], workspace)


def _resolved_path(path: Path) -> Path | None:
    try:
        resolved = path.resolve()
    except OSError:
        return None
    return resolved if resolved.exists() else None


def _resolved_pasted_path(source: str, workspace: Path) -> Path | None:
    value = source.strip().strip('"').strip("'")
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme == "file":
        return _resolved_path(Path(unquote(parsed.path)))
    if parsed.scheme:
        return None
    path = _workspace_path(value, workspace)
    if (resolved := _resolved_path(path)) is not None:
        return resolved
    if escaped := _shell_escaped_path(value, workspace):
        return _resolved_path(escaped)
    return None


def _is_image_path(path: Path) -> bool:
    mime_type, _ = mimetypes.guess_type(path.name)
    return path.is_file() and (
        (mime_type or "").startswith("image/")
        or path.suffix.lower() in IMAGE_EXTENSIONS
    )


def _clipboard_image_bytes() -> bytes | None:
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


def pasted_image_path(session: sessions.Session, text: str) -> Path | None:
    workspace = Path(sessions.get_messages(session)["workspace"])
    if (path := _resolved_pasted_path(text, workspace)) and _is_image_path(path):
        return path
    return None


def save_clipboard_image(session: sessions.Session) -> Path | None:
    if not (data := _clipboard_image_bytes()):
        return None
    path = _attachment_path(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path
