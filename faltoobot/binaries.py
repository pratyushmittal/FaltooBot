import os
import platform
import shutil
import subprocess

from pathlib import Path

from faltoobot.config import Config, load_toml, merge_config, render_config


PACKAGES = {"pandoc": "pandoc", "mutool": "mupdf-tools"}
BREW_PACKAGES = {"pandoc": "pandoc", "mutool": "mupdf"}


def _packages(names: list[str]) -> list[str]:
    packages = BREW_PACKAGES if platform.system() == "Darwin" else PACKAGES
    return sorted({packages[name] for name in names})


def install_document_binaries(names: list[str]) -> bool:
    packages = _packages(names)
    if platform.system() == "Darwin" and shutil.which("brew"):
        return (
            subprocess.run(
                ["brew", "install", *packages], check=False, timeout=900
            ).returncode
            == 0
        )
    if shutil.which("apt-get"):
        sudo = [] if getattr(os, "geteuid", lambda: 1)() == 0 else ["sudo", "-n"]
        subprocess.run([*sudo, "apt-get", "update"], check=False, timeout=900)
        return (
            subprocess.run(
                [*sudo, "apt-get", "install", "-y", *packages], check=False, timeout=900
            ).returncode
            == 0
        )
    return False


def ensure_document_binaries(config: Config) -> None:
    data = merge_config(load_toml(config.config_file))
    doc = data["document"]
    common = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"]

    def find(name: str) -> str:
        configured = str(doc.get(f"{name}_binary") or "")
        if configured and Path(configured).exists():
            return configured
        found = shutil.which(name)
        if found:
            return found
        return next(
            (str(path) for root in common if (path := Path(root) / name).exists()), ""
        )

    missing = [name for name in PACKAGES if not find(name)]
    if missing:
        install_document_binaries(missing)
    for name in PACKAGES:
        doc[f"{name}_binary"] = find(name)
    config.config_file.parent.mkdir(parents=True, exist_ok=True)
    config.config_file.write_text(render_config(data), encoding="utf-8")
