import json
import os
import subprocess
from pathlib import Path
from typing import Literal, TypedDict

from openai import AsyncOpenAI

from faltoobot.config import Config

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


def message(role: Literal["user", "assistant", "system", "developer"], content: str) -> Message:
    return {"type": "message", "role": role, "content": content}


def assistant_message(
    content: str, phase: Literal["commentary", "final_answer"] = "final_answer"
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
    skills = [
        skill
        for path in sorted(skills_dir(config).iterdir())
        if path.is_dir()
        for skill in [skill_meta(path)]
        if skill
    ]
    return skills


def read_skill(config: Config, name: str) -> str:
    for skill in list_skills(config):
        if skill["name"] == name or Path(skill["path"]).name == name:
            skill_md = skill_file(Path(skill["path"]))
            if skill_md:
                return skill_md.read_text(encoding="utf-8")
    raise ValueError(f"Unknown skill: {name}")


def skill_tool() -> dict[str, object]:
    return {
        "type": "function",
        "name": "skills",
        "description": "List local skills or read a specific skill from ~/.faltoobot/skills/.",
        "strict": True,
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


def shell_tool(config: Config) -> dict[str, object]:
    return {
        "type": "shell",
        "environment": {
            "type": "local",
            "skills": list_skills(config),
        },
    }


def web_search_tool() -> dict[str, object]:
    return {"type": "web_search"}


def tools(config: Config) -> list[dict[str, object]]:
    return [shell_tool(config), web_search_tool(), skill_tool()]


def input_messages(messages: list[Message]) -> list[dict[str, str]]:
    return [
        {key: value for key, value in msg.items() if key in {"type", "role", "content", "phase"}}
        for msg in messages
    ]


def input_items(items: list[object]) -> list[object]:
    return [item.to_dict() if hasattr(item, "to_dict") else item for item in items]


def prune_items(items: list[object]) -> list[object]:
    for index in range(len(items) - 1, -1, -1):
        item = items[index]
        if isinstance(item, dict) and item.get("type") == "compaction":
            return items[index:]
    return items


def run_shell_call(item: dict[str, object]) -> dict[str, object]:
    action = item["action"]
    if not isinstance(action, dict):
        raise ValueError("Invalid shell action")
    commands = action.get("commands")
    if not isinstance(commands, list):
        raise ValueError("Invalid shell commands")
    timeout_ms = action.get("timeout_ms")
    timeout = (timeout_ms / 1000) if isinstance(timeout_ms, int) else DEFAULT_TIMEOUT_MS / 1000
    try:
        process = subprocess.run(
            ["/bin/bash", "-lc", "\n".join(str(command) for command in commands)],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(Path.cwd()),
        )
        output = {
            "stdout": process.stdout[:MAX_SHELL_OUTPUT],
            "stderr": process.stderr[:MAX_SHELL_OUTPUT],
            "outcome": {"type": "exit", "exit_code": process.returncode},
        }
    except subprocess.TimeoutExpired as exc:
        output = {
            "stdout": (exc.stdout or "")[:MAX_SHELL_OUTPUT],
            "stderr": (exc.stderr or "")[:MAX_SHELL_OUTPUT],
            "outcome": {"type": "timeout"},
        }
    return {
        "type": "shell_call_output",
        "call_id": item["call_id"],
        "status": "completed",
        "max_output_length": MAX_SHELL_OUTPUT,
        "output": [output],
    }


def run_local_shell_call(item: dict[str, object]) -> dict[str, object]:
    action = item["action"]
    if not isinstance(action, dict):
        raise ValueError("Invalid local shell action")
    command = action.get("command")
    if not isinstance(command, list):
        raise ValueError("Invalid local shell command")
    env = action.get("env")
    timeout_ms = action.get("timeout_ms")
    timeout = (timeout_ms / 1000) if isinstance(timeout_ms, int) else DEFAULT_TIMEOUT_MS / 1000
    try:
        process = subprocess.run(
            [str(part) for part in command],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=action.get("working_directory") or str(Path.cwd()),
            env={**os.environ, **(env if isinstance(env, dict) else {})},
        )
        result: ShellResult = {
            "stdout": process.stdout[:MAX_SHELL_OUTPUT],
            "stderr": process.stderr[:MAX_SHELL_OUTPUT],
            "exit_code": process.returncode,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        result = {
            "stdout": (exc.stdout or "")[:MAX_SHELL_OUTPUT],
            "stderr": (exc.stderr or "")[:MAX_SHELL_OUTPUT],
            "exit_code": None,
            "timed_out": True,
        }
    return {
        "type": "local_shell_call_output",
        "id": item["call_id"],
        "status": "completed",
        "output": json.dumps(result),
    }


def run_skill_call(config: Config, item: dict[str, object]) -> dict[str, object]:
    args = json.loads(item.get("arguments") or "{}")
    if not isinstance(args, dict):
        raise ValueError("Invalid skills tool arguments")
    action = args.get("action")
    if action == "list":
        output = json.dumps(list_skills(config), ensure_ascii=False)
    elif action == "read":
        name = args.get("name")
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


def tool_outputs(config: Config, items: list[dict[str, object]]) -> list[dict[str, object]]:
    outputs: list[dict[str, object]] = []
    for item in items:
        item_type = item.get("type")
        if item_type == "shell_call":
            outputs.append(run_shell_call(item))
        elif item_type == "local_shell_call":
            outputs.append(run_local_shell_call(item))
        elif item_type == "function_call" and item.get("name") == "skills":
            outputs.append(run_skill_call(config, item))
    return outputs


async def reply(openai_client: AsyncOpenAI, config: Config, messages: list[Message]) -> str:
    items: list[object] = input_messages(messages)
    while True:
        response = await openai_client.responses.create(
            model=config.openai_model,
            input=items,
            instructions=config.system_prompt,
            store=False,
            parallel_tool_calls=False,
            include=["reasoning.encrypted_content", "web_search_call.action.sources"],
            context_management=[{"type": "compaction", "compact_threshold": COMPACT_THRESHOLD}],
            tools=tools(config),
        )
        outputs = input_items(response.output)
        items = prune_items([*items, *outputs])
        next_items = tool_outputs(config, [item for item in outputs if isinstance(item, dict)])
        if not next_items:
            text = (response.output_text or "").strip()
            return text or "I couldn't generate a reply just now."
        items.extend(next_items)
