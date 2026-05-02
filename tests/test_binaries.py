from pathlib import Path

from faltoobot import binaries
from faltoobot.config import Config, render_config


def config(tmp_path: Path) -> Config:
    root = tmp_path / "root"
    root.mkdir()
    path = root / "config.toml"
    path.write_text(render_config({}), encoding="utf-8")
    return Config(
        home=tmp_path,
        root=root,
        config_file=path,
        log_file=root / "log",
        sessions_dir=root / "sessions",
        session_db=root / "session.db",
        launch_agent=root / "agent.plist",
        run_script=root / "run.sh",
        openai_api_key="",
        openai_oauth="",
        openai_model="gpt-5.4",
        openai_thinking="high",
        openai_fast=False,
        openai_transcription_model="gpt-4o-transcribe",
        allow_group_chats=set(),
        allowed_chats=set(),
        bot_name="Faltoo",
        browser_binary="",
    )


def test_ensure_document_binaries_saves_found_paths(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(binaries.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(binaries, "install_document_binaries", lambda packages: False)
    cfg = config(tmp_path)

    binaries.ensure_document_binaries(cfg)

    text = cfg.config_file.read_text(encoding="utf-8")
    assert 'pandoc_binary = "/bin/pandoc"' in text
    assert 'mutool_binary = "/bin/mutool"' in text


def test_ensure_document_binaries_installs_missing(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []
    seen = {"count": 0}

    def which(name: str) -> str | None:
        return f"/bin/{name}" if seen["count"] else None

    def install(packages: list[str]) -> bool:
        calls.append(packages)
        seen["count"] += 1
        return True

    monkeypatch.setattr(binaries.shutil, "which", which)
    monkeypatch.setattr(binaries.Path, "exists", lambda self: False)
    monkeypatch.setattr(binaries, "install_document_binaries", install)

    binaries.ensure_document_binaries(config(tmp_path))

    assert calls == [["pandoc", "mutool"]]


def test_ensure_document_binaries_finds_homebrew_when_path_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    def exists(self: Path) -> bool:
        return str(self) in {"/opt/homebrew/bin/pandoc", "/opt/homebrew/bin/mutool"}

    monkeypatch.setattr(binaries.shutil, "which", lambda name: None)
    monkeypatch.setattr(binaries.Path, "exists", exists)
    monkeypatch.setattr(binaries, "install_document_binaries", lambda packages: False)
    cfg = config(tmp_path)

    binaries.ensure_document_binaries(cfg)

    text = cfg.config_file.read_text(encoding="utf-8")
    assert 'pandoc_binary = "/opt/homebrew/bin/pandoc"' in text
    assert 'mutool_binary = "/opt/homebrew/bin/mutool"' in text


def test_document_binary_packages_use_homebrew_mupdf(monkeypatch) -> None:
    monkeypatch.setattr(binaries.platform, "system", lambda: "Darwin")

    assert binaries._packages(["pandoc", "mutool"]) == ["mupdf", "pandoc"]


def test_document_binary_packages_use_apt_mupdf_tools(monkeypatch) -> None:
    monkeypatch.setattr(binaries.platform, "system", lambda: "Linux")

    assert binaries._packages(["pandoc", "mutool"]) == ["mupdf-tools", "pandoc"]
