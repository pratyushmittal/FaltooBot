import os
import subprocess
from pathlib import Path

import pytest

from faltoobot.faltoochat import xray


def config_text(api_key: str) -> str:
    return "\n".join(
        [
            "# Faltoobot config",
            "",
            "[openai]",
            f'api_key = "{api_key}"',
            'model = "gpt-5.4-nano"',
            'thinking = "none"',
            "fast = true",
            "",
            "[bot]",
            "allow_groups = false",
            "allowed_chats = []",
            "",
        ]
    )


def git(workspace: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.anyio
async def test_xray_overviews_e2e(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if os.environ.get("RUN_FALTOOCHAT_E2E") != "1":
        pytest.skip("Set RUN_FALTOOCHAT_E2E=1 to run the E2E test.")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY must be set to run this E2E test.")

    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = home / ".faltoobot" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config_text(api_key), encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))

    git(workspace, "init")
    git(workspace, "config", "user.email", "tests@example.com")
    git(workspace, "config", "user.name", "Tests")

    file_path = workspace / "alpha.py"
    file_path.write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n",
        encoding="utf-8",
    )
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "initial")

    file_path.write_text(
        "def add(a: int, b: int) -> int:\n    total = a + b\n    return total\n",
        encoding="utf-8",
    )

    file_overview = await xray.get_file_overview(workspace, file_path)
    change_overview = await xray.get_change_overview(workspace, [file_path])

    assert file_overview.important_changes
    assert change_overview.summary.strip()
    assert change_overview.files
    assert change_overview.files[0].path == "alpha.py"
