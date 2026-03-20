import subprocess
from pathlib import Path

PROMPT = (
    "Check changes since last version release. Create `migrations` scripts as needed"
)


def ensure_repo_root(path: Path) -> Path:
    if not ((path / "pyproject.toml").is_file() and (path / "migrations").is_dir()):
        raise SystemExit("makemigrations is only for the faltoobot repo")
    return path


def main() -> int:
    root = ensure_repo_root(Path.cwd())
    subprocess.run(
        ["uv", "run", "faltoochat", PROMPT, "--new-session"],
        check=True,
        cwd=root,
    )
    return 0
