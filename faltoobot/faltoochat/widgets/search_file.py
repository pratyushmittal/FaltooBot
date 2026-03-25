import subprocess
from pathlib import Path
from typing import cast

from ..git import is_git_workspace
from .telescope import Telescope


class SearchFile(Telescope[Path]):
    def __init__(self, *, workspace: Path, title: str, placeholder: str) -> None:
        files = cast(list[Path], sorted(_project_files(workspace), key=str))
        super().__init__(
            items=files,
            title=title,
            placeholder=placeholder,
        )


def _project_files(workspace: Path) -> list[Path]:
    if is_git_workspace(workspace):
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return [Path(path) for path in result.stdout.split("\0") if path]

    return cast(
        list[Path],
        sorted(
            [
                Path(str(path.relative_to(workspace)))
                for path in workspace.rglob("*")
                if path.is_file() and ".git" not in path.parts
            ],
            key=str,
        ),
    )
