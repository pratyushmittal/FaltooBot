from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, scenarios, then, when

from faltoobot import tools
from faltoobot.gpt_utils import get_tools_definition

scenarios("features/shell_tool_python_hint.feature")


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    # comment: a bare HOME keeps the user's shell profiles from adding their python.
    monkeypatch.setenv("HOME", str(tmp_path))
    tools._python_hint.cache_clear()
    return {"tmp_path": tmp_path}


@given("the machine has python3 but no python command")
def no_python(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")


@given("the machine has a python command")
def has_python(ctx: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = ctx["tmp_path"] / "bin"
    bin_dir.mkdir()
    (bin_dir / "python").write_text("#!/bin/sh\n")
    (bin_dir / "python").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")


@when("the assistant gets the shell tool")
def get_shell_tool(ctx: dict[str, Any]) -> None:
    tool = tools.get_run_shell_call_tool(ctx["tmp_path"])
    ctx["description"] = get_tools_definition(tool)["description"]


@then("the tool description says to use uv run python")
def says_use_uv(ctx: dict[str, Any]) -> None:
    assert "There is no `python` command" in ctx["description"]
    assert "Use `uv run python`" in ctx["description"]
    assert "python3" not in ctx["description"]


@then("the tool description does not mention a missing python command")
def no_python_hint(ctx: dict[str, Any]) -> None:
    assert "There is no `python` command" not in ctx["description"]
