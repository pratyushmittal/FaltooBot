import json
import os
import subprocess
from pathlib import Path
from typing import Any, Literal, TypedDict

from openai import AsyncOpenAI

from faltoobot.config import Config
from faltoobot.store import Session

COMPACT_THRESHOLD = 100_000
DEFAULT_TIMEOUT_MS = 60_000
MAX_SHELL_OUTPUT = 12_000


class Message(TypedDict, total=False):
    role: Literal["user", "assistant", "system", "developer"]
    content: str
    phase: Literal["commentary", "final_answer"]
    type: Literal["message"]


class Skill(TypedDict):
    name: str
    description: str
    path: str


class ShellResult(TypedDict):
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool


class ReplyResult(TypedDict):
    text: str
    output_items: list[dict[str, Any]]
    usage: dict[str, Any] | None


def agents_file(path: Path) -> Path:
    return path / "AGENTS.md"


def read_agents_text(path: Path) -> str | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def instruction_parts(config: Config, session: Session) -> list[str]:
    parts = [config.system_prompt]
    seen = set[Path]()
    for base, label in (
        (config.root, "Global AGENTS.md"),
        (session.workspace, "Session AGENTS.md"),
    ):
        agents_path = agents_file(base).resolve()
        if agents_path in seen:
            continue
        seen.add(agents_path)
        if text := read_agents_text(agents_path):
            parts.append(f"{label}:\n{text}")
    return parts


def system_instructions(config: Config, session: Session) -> str:
    return "\n\n".join(part for part in instruction_parts(config, session) if part)


def reasoning_config(config: Config) -> dict[str, str]:
    return {"effort": config.openai_thinking, "summary": "auto"}


def message(role: Literal["user", "assistant", "system", "developer"], content: str) -> Message:
    return {"type": "message", "role": role, "content": content}


def assistant_message(
    content: str,
    phase: Literal["commentary", "final_answer"] = "final_answer",
) -> Message:
    return {"type": "message", "role": "assistant", "content": content, "phase": phase}


def skills_dir(config: Config) -> Path:
    path = config.root / "skills"
    path.mkdir(parents=True, exist_ok=True)
    return path


def skill_file(path: Path) -> Path | None:
    for name in ("SKILL.md", "skill.md"):
        candidate = path / name
        if candidate.exists():
            return candidate
    return None


def skill_meta(path: Path) -> Skill | None:
    skill_md = skill_file(path)
    if not skill_md:
        return None
    text = skill_md.read_text(encoding="utf-8")
    name = path.name
    description = ""
    if text.startswith("---\n"):
        parts = text.split("\n---\n", 1)
        if len(parts) == 2:
            for line in parts[0].splitlines()[1:]:
                if line.startswith("name:"):
                    name = line.split(":", 1)[1].strip() or name
                if line.startswith("description:"):
                    description = line.split(":", 1)[1].strip()
    return {"name": name, "description": description, "path": str(path)}


def list_skills(config: Config) -> list[Skill]:
    return [
        skill
        for path in sorted(skills_dir(config).iterdir())
        if path.is_dir()
        for skill in [skill_meta(path)]
        if skill
    ]


def read_skill(config: Config, name: str) -> str:
    for skill in list_skills(config):
        if skill["name"] == name or Path(skill["path"]).name == name:
            skill_md = skill_file(Path(skill["path"]))
            if skill_md:
                return skill_md.read_text(encoding="utf-8")
    raise ValueError(f"Unknown skill: {name}")


def skill_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "name": "skills",
        "description": "List local skills or read a specific skill from ~/.faltoobot/skills/.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "read"]},
                "name": {"type": "string"},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    }


def shell_tool(config: Config) -> dict[str, Any]:
    return {"type": "shell", "environment": {"type": "local"}}


def web_search_tool() -> dict[str, Any]:
    return {"type": "web_search"}


def tools(config: Config) -> list[dict[str, Any]]:
    return [shell_tool(config), web_search_tool(), skill_tool()]


def input_messages(messages: list[Any]) -> list[Any]:
    return messages


def input_items(items: list[Any]) -> list[Any]:
    return [item.to_dict() if hasattr(item, "to_dict") else item for item in items]


def response_usage(response: Any) -> dict[str, Any] | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    if hasattr(usage, "to_dict"):
        usage = usage.to_dict()
    return usage if isinstance(usage, dict) else None


def prune_items(items: list[Any]) -> list[Any]:
    for index in range(len(items) - 1, -1, -1):
        item = items[index]
        if isinstance(item, dict) and item.get("type") == "compaction":
            return items[index:]
    return items


def clipped_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        value = value.decode(errors="replace")
    return (value or "")[:MAX_SHELL_OUTPUT]


def shell_output_limit(action: dict[str, Any]) -> int:
    value = action.get("max_output_length")
    return value if isinstance(value, int) and value > 0 else MAX_SHELL_OUTPUT


def run_shell_call(session: Session, item: dict[str, Any]) -> dict[str, Any]:
    action = item["action"]
    commands = action["commands"]
    max_output_length = shell_output_limit(action)
    timeout_ms = action.get("timeout_ms")
    timeout = (timeout_ms / 1000) if isinstance(timeout_ms, int) else DEFAULT_TIMEOUT_MS / 1000
    try:
        process = subprocess.run(
            ["/bin/bash", "-lc", "\n".join(str(command) for command in commands)],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(session.workspace),
        )
        output = {
            "stdout": clipped_text(process.stdout),
            "stderr": clipped_text(process.stderr),
            "outcome": {"type": "exit", "exit_code": process.returncode},
        }
    except subprocess.TimeoutExpired as exc:
        output = {
            "stdout": clipped_text(exc.stdout),
            "stderr": clipped_text(exc.stderr),
            "outcome": {"type": "timeout"},
        }
    return {
        "type": "shell_call_output",
        "call_id": item["call_id"],
        "status": "completed",
        "max_output_length": max_output_length,
        "output": [output],
    }


def run_local_shell_call(session: Session, item: dict[str, Any]) -> dict[str, Any]:
    action = item["action"]
    timeout_ms = action.get("timeout_ms")
    timeout = (timeout_ms / 1000) if isinstance(timeout_ms, int) else DEFAULT_TIMEOUT_MS / 1000
    try:
        process = subprocess.run(
            [str(part) for part in action["command"]],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=action.get("working_directory") or str(session.workspace),
            env={
                **os.environ,
                **(action.get("env") if isinstance(action.get("env"), dict) else {}),
            },
        )
        result: ShellResult = {
            "stdout": clipped_text(process.stdout),
            "stderr": clipped_text(process.stderr),
            "exit_code": process.returncode,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        result = {
            "stdout": clipped_text(exc.stdout),
            "stderr": clipped_text(exc.stderr),
            "exit_code": None,
            "timed_out": True,
        }
    return {
        "type": "local_shell_call_output",
        "id": item["call_id"],
        "status": "completed",
        "output": json.dumps(result),
    }


def run_skill_call(config: Config, item: dict[str, Any]) -> dict[str, Any]:
    arguments = item.get("arguments")
    args = json.loads(arguments) if isinstance(arguments, str) else {}
    action = args.get("action") if isinstance(args, dict) else None
    if action == "list":
        output = json.dumps(list_skills(config), ensure_ascii=False)
    elif action == "read":
        name = args.get("name") if isinstance(args, dict) else None
        if not isinstance(name, str):
            raise ValueError("skills.read requires a name")
        output = read_skill(config, name)
    else:
        raise ValueError(f"Unknown skills action: {action}")
    return {
        "type": "function_call_output",
        "call_id": item["call_id"],
        "output": output,
        "status": "completed",
    }


def tool_outputs(config: Config, session: Session, items: list[Any]) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "shell_call":
            outputs.append(run_shell_call(session, item))
        elif item_type == "local_shell_call":
            outputs.append(run_local_shell_call(session, item))
        elif item_type == "function_call" and item.get("name") == "skills":
            outputs.append(run_skill_call(config, item))
    return outputs


async def reply(
    openai_client: AsyncOpenAI,
    config: Config,
    session: Session,
    messages: list[Any],
) -> ReplyResult:
    items: list[Any] = input_messages(messages)
    instructions = system_instructions(config, session)
    while True:
        response = await openai_client.responses.create(
            model=config.openai_model,
            input=items,  # type: ignore[arg-type]
            instructions=instructions,
            reasoning=reasoning_config(config),
            store=False,
            parallel_tool_calls=True,
            include=["reasoning.encrypted_content", "web_search_call.action.sources"],
            context_management=[{"type": "compaction", "compact_threshold": COMPACT_THRESHOLD}],
            tools=tools(config),  # type: ignore[arg-type]
        )
        outputs = input_items(response.output)
        items = prune_items([*items, *outputs])
        next_items = tool_outputs(config, session, outputs)
        if not next_items:
            text = (response.output_text or "").strip()
            return {
                "text": text or "I couldn't generate a reply just now.",
                "output_items": [item for item in outputs if isinstance(item, dict)],
                "usage": response_usage(response),
            }
        items.extend(next_items)
