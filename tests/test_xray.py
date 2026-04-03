from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from faltoobot.config import Config
from faltoobot.faltoochat import xray


class FakeStream:
    def __init__(self, result) -> None:
        self.result = result

    async def __aenter__(self) -> "FakeStream":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def __aiter__(self):
        async def iterator():
            if False:
                yield None

        return iterator()

    async def get_final_response(self):
        return SimpleNamespace(output_parsed=self.result)


class FakeResponses:
    def __init__(self, result, calls: list[dict[str, object]]) -> None:
        self.result = result
        self.calls = calls

    def stream(self, **kwargs: Any) -> FakeStream:
        self.calls.append(kwargs)
        return FakeStream(self.result)


class FakeClient:
    def __init__(self, result, calls: list[dict[str, object]]) -> None:
        self.responses = FakeResponses(result, calls)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _config() -> Config:
    return cast(Config, SimpleNamespace(openai_fast=False))


@pytest.mark.anyio
async def test_get_file_overview_uses_numbered_file_content(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file_path = workspace / "alpha.py"
    file_path.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    calls: list[dict[str, object]] = []
    client = FakeClient(
        xray.FileOverview(
            important_changes=[
                xray.FileSymbolOverview(
                    name="alpha",
                    summary="Changed alpha.",
                    line_number=1,
                )
            ],
        ),
        calls,
    )
    monkeypatch.setattr(xray, "_build_client", lambda config: client)
    monkeypatch.setattr(
        xray,
        "get_diff",
        lambda path: [
            {"is_staged": False, "type": "+", "text": "def alpha():"},
            {"is_staged": False, "type": "+", "text": "    return 1"},
        ],
    )

    await xray.get_file_overview(workspace, file_path, _config())

    assert calls[0]["model"] == xray.XRAY_MODEL
    assert (
        calls[0]["instructions"]
        == "Return a structured source-file overview for code review."
    )
    input_items = calls[0]["input"]
    assert isinstance(input_items, list)
    assert calls[0]["reasoning"] == {"effort": "high"}
    assert "Return a reviewer-friendly list of the most important changes" in str(
        calls[0]["input"]
    )
    assert "Use the DIFF to identify what changed" in str(calls[0]["input"])
    assert "DIFF:" in str(input_items)
    assert "+def alpha():" in str(input_items)
    assert "1 | def alpha():" in str(input_items)
    assert "2 |     return 1" in str(input_items)
    assert client.closed is True


@pytest.mark.anyio
async def test_get_change_overview_uses_diff_input(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file_path = workspace / "alpha.py"
    file_path.write_text("value = 2\n", encoding="utf-8")
    calls: list[dict[str, object]] = []
    client = FakeClient(
        xray.ChangeOverview(
            summary="One file changed.",
            files=[
                xray.ChangeFileOverview(
                    path="alpha.py",
                    about="Updates the stored value.",
                )
            ],
        ),
        calls,
    )
    monkeypatch.setattr(xray, "_build_client", lambda config: client)
    monkeypatch.setattr(
        xray,
        "get_diff",
        lambda path: [{"is_staged": False, "type": "+", "text": "value = 2"}],
    )

    overview = await xray.get_change_overview(workspace, [file_path], _config())

    assert overview.summary == "One file changed."
    assert (
        calls[0]["instructions"]
        == "Return a structured change overview for a git review."
    )
    input_items = calls[0]["input"]
    assert isinstance(input_items, list)
    assert "FILE: alpha.py" in str(input_items)
    assert "+value = 2" in str(input_items)
    assert client.closed is True
