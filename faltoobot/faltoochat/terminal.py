import os
import re
import select
import subprocess
import sys
import termios
import time
import tty
from pathlib import Path

from faltoobot.config import Config

HEX_CHANNEL_SHORT = 2
HEX_CHANNEL_LONG = 4
DARK_BACKGROUND_THRESHOLD = 0.5


def status_text(config: Config) -> str:
    model = (
        f"{config.openai_model} (fast)" if config.openai_fast else config.openai_model
    )
    return f"model: {model}  thinking: {config.openai_thinking}"


def _channel_value(value: str) -> int:
    if len(value) == HEX_CHANNEL_SHORT:
        return int(value, 16)
    if len(value) == HEX_CHANNEL_LONG:
        return int(value[:2], 16)
    return int(value[:2], 16)


def terminal_background_dark(timeout: float = 0.1) -> bool | None:
    """Detect whether the current terminal background is dark.

    Sends OSC 11 to ask the terminal for its background color, parses the
    returned RGB value, and converts it to a simple brightness score.

    Returns:
        True: Background looks dark.
        False: Background looks light.
        None: Detection is unavailable, unsupported, or timed out.
    """
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return None
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        os.write(sys.stdout.fileno(), b"\x1b]11;?\x07")
        end = time.monotonic() + timeout
        data = bytearray()
        while time.monotonic() < end:
            ready, _, _ = select.select([fd], [], [], end - time.monotonic())
            if not ready:
                break
            data.extend(os.read(fd, 256))
            if b"\x07" in data or b"\x1b\\" in data:
                break
    except OSError:
        return None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

    match = re.search(
        rb"11;rgb:([0-9a-fA-F]{2,4})/([0-9a-fA-F]{2,4})/([0-9a-fA-F]{2,4})",
        bytes(data),
    )
    if match is None:
        return None
    red, green, blue = (_channel_value(value.decode()) for value in match.groups())
    brightness = (0.299 * red + 0.587 * green + 0.114 * blue) / 255
    return brightness < DARK_BACKGROUND_THRESHOLD


def textual_theme_from_terminal(timeout: float = 0.1) -> str | None:
    if (terminal_dark := terminal_background_dark(timeout)) is None:
        return None
    return "textual-dark" if terminal_dark else "textual-light"


def input_hint(
    config: Config,
    *,
    replying: bool = False,
    queued: int = 0,
) -> str:
    parts = [status_text(config)]
    if replying:
        parts.append("replying")
    if queued:
        parts.append(f"queued {queued}")
    return "  ".join(parts)


def open_in_default_editor(path: Path) -> None:
    command = (
        ["open", str(path)] if sys.platform == "darwin" else ["xdg-open", str(path)]
    )
    subprocess.Popen(command)  # noqa: S603
