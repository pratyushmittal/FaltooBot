from pathlib import Path

from faltoobot.agent import run_shell_call
from faltoobot.store import create_cli_session


def test_run_shell_call_preserves_requested_max_output_length(tmp_path: Path) -> None:
    session = create_cli_session(tmp_path / "sessions", "CLI shell test")
    output = run_shell_call(
        session,
        {
            "type": "shell_call",
            "call_id": "call_123",
            "action": {
                "commands": ["pwd"],
                "max_output_length": 4000,
            },
        },
    )

    assert output["type"] == "shell_call_output"
    assert output["call_id"] == "call_123"
    assert output["max_output_length"] == 4000
