import json
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, scenarios, then, when

from faltoobot.tools import MAX_SHELL_OUTPUT, get_run_shell_call_tool

scenarios("features/shell_output_clipping.feature")


@pytest.fixture
def ctx() -> dict[str, Any]:
    return {}


@given("a command prints a long log and ends with a test summary")
def long_command(ctx: dict[str, Any]) -> None:
    ctx["command"] = (
        "echo 'collected 3 items'; "
        'for i in $(seq 1 3000); do echo "log line $i"; done; '
        "echo 'FAILED tests/test_app.py::test_save - AssertionError'; "
        "echo '1 failed, 2 passed in 0.12s'; exit 1"
    )


@when("the assistant runs it with the shell tool")
def run_command(ctx: dict[str, Any], tmp_path: Path) -> None:
    run_shell_call = get_run_shell_call_tool(tmp_path)
    ctx["result"] = json.loads(run_shell_call(ctx["command"], "run tests", 10_000))


@then("the output keeps the start and the test summary")
def keeps_start_and_summary(ctx: dict[str, Any]) -> None:
    stdout = ctx["result"]["stdout"]
    assert stdout.startswith("collected 3 items")
    assert "FAILED tests/test_app.py::test_save" in stdout
    assert stdout.rstrip().endswith("1 failed, 2 passed in 0.12s")
    assert len(stdout) <= MAX_SHELL_OUTPUT + 200


@then("the output says how much was cut from the middle")
def says_what_was_cut(ctx: dict[str, Any]) -> None:
    assert "characters cut from the middle" in ctx["result"]["stdout"]
