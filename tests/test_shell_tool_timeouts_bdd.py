import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, scenarios, then, when

from faltoobot.tools import run_shell_call_in_workspace

scenarios("features/shell_tool_timeouts.feature")


@pytest.fixture
def ctx(tmp_path: Path) -> dict[str, Any]:
    (tmp_path / "notes.txt").write_text("needle in the haystack\n")
    return {"workspace": tmp_path}


@given("faltoochat runs with stdin connected to an open pipe")
def headless_stdin() -> None:
    if not shutil.which("rg"):
        pytest.skip("rg is not installed")


@when("the assistant searches the workspace with rg and no path")
def search_without_path(ctx: dict[str, Any]) -> None:
    # comment: run the tool in a child with a piped stdin, like a headless faltoochat.
    script = (
        "import sys; from faltoobot.tools import run_shell_call_in_workspace; "
        "print(run_shell_call_in_workspace(sys.argv[1], 'rg -n needle', 3000))"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(ctx["workspace"])],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    stdout, _ = child.communicate(timeout=20)
    ctx["result"] = json.loads(stdout)


@then("the search returns the match without timing out")
def search_finished(ctx: dict[str, Any]) -> None:
    assert not ctx["result"]["timed_out"]
    assert "needle in the haystack" in ctx["result"]["stdout"]


@when("the assistant runs a Python script that prints and then hangs")
def python_hangs(ctx: dict[str, Any]) -> None:
    command = f"'{sys.executable}' -c \"print('started'); import time; time.sleep(5)\""
    ctx["result"] = json.loads(
        run_shell_call_in_workspace(str(ctx["workspace"]), command, 1000)
    )


@then("the timed-out output still shows what the script printed")
def timed_out_output_kept(ctx: dict[str, Any]) -> None:
    assert ctx["result"]["timed_out"]
    assert ctx["result"]["stdout"] == "started\n"
