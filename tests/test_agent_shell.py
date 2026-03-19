from pathlib import Path

from faltoobot.agent import run_shell_call
from faltoobot.store import create_session

MAX_OUTPUT_LENGTH = 4000


def test_run_shell_call_preserves_requested_max_output_length(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = create_session(
        tmp_path / "sessions", "CLI shell test", kind="cli", workspace=workspace
    )
    output = run_shell_call(
        session,
        {
            "type": "shell_call",
            "call_id": "call_123",
            "action": {
                "commands": ["pwd"],
                "max_output_length": MAX_OUTPUT_LENGTH,
            },
        },
    )

    assert output["type"] == "shell_call_output"
    assert output["call_id"] == "call_123"
    assert output["max_output_length"] == MAX_OUTPUT_LENGTH


def test_run_shell_call_replaces_invalid_utf8_bytes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = create_session(
        tmp_path / "sessions", "CLI shell test", kind="cli", workspace=workspace
    )
    output = run_shell_call(
        session,
        {
            "type": "shell_call",
            "call_id": "call_456",
            "action": {
                "commands": [
                    'python3 -c "import sys; sys.stdout.buffer.write(bytes([0xF3]))"'
                ],
            },
        },
    )

    result = output["output"][0]
    assert result["stdout"] == "�"
    assert result["outcome"] == {"type": "exit", "exit_code": 0}
