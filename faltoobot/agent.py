import asyncio
import inspect
import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict

from openai import AsyncOpenAI

from faltoobot.config import Config
from faltoobot.store import Session

COMPACT_THRESHOLD = 100_000
DEFAULT_TIMEOUT_MS = 60_000
MAX_SHELL_OUTPUT = 12_000


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
    instructions: str


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
        (Path.home(), "Home AGENTS.md"),
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


def tools() -> list[dict[str, Any]]:
    return [
        {"type": "shell", "environment": {"type": "local"}},
        {"type": "web_search"},
        skill_tool(),
    ]


def sanitize_input(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: sanitize_input(item)
            for key, item in value.items()
            if not str(key).startswith("parsed_")
        }
    if isinstance(value, list):
        return [sanitize_input(item) for item in value]
    return value


def normalized_items(items: list[Any]) -> list[Any]:
    return [sanitize_input(item.to_dict() if hasattr(item, "to_dict") else item) for item in items]


def dict_item(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def usage_dict(response: Any) -> dict[str, Any] | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    if hasattr(usage, "to_dict"):
        usage = usage.to_dict()
    return usage if isinstance(usage, dict) else None


def output_text_from_items(items: list[Any]) -> str:
    texts: list[str] = []
    for item in items:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "output_text":
                continue
            text = part.get("text")
            if isinstance(text, str):
                texts.append(text)
    return "".join(texts)


def response_text(response: Any, response_outputs: list[dict[str, Any]] | None = None) -> str:
    text = getattr(response, "output_text", "")
    if isinstance(text, str) and text.strip():
        return text.strip()
    items = response_outputs or normalized_items(getattr(response, "output", []))
    return output_text_from_items(items).strip()


def compacted_items(items: list[Any]) -> list[Any]:
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


def timeout_seconds(action: dict[str, Any]) -> float:
    timeout_ms = action.get("timeout_ms")
    timeout = timeout_ms if isinstance(timeout_ms, int) else DEFAULT_TIMEOUT_MS
    return timeout / 1000


def run_shell_call(session: Session, item: dict[str, Any]) -> dict[str, Any]:
    action = item["action"]
    max_output_length = shell_output_limit(action)
    try:
        process = subprocess.run(
            ["/bin/bash", "-lc", "\n".join(str(command) for command in action["commands"])],
            capture_output=True,
            text=True,
            timeout=timeout_seconds(action),
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
    try:
        process = subprocess.run(
            [str(part) for part in action["command"]],
            capture_output=True,
            text=True,
            timeout=timeout_seconds(action),
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


def needs_tool_output(item: dict[str, Any]) -> bool:
    match item.get("type"), item.get("name"):
        case ("shell_call", _) | ("local_shell_call", _) | ("function_call", "skills"):
            return True
        case _:
            return False


def tool_output(config: Config, session: Session, item: dict[str, Any]) -> dict[str, Any] | None:
    match item.get("type"), item.get("name"):
        case "shell_call", _:
            return run_shell_call(session, item)
        case "local_shell_call", _:
            return run_local_shell_call(session, item)
        case "function_call", "skills":
            return run_skill_call(config, item)
        case _:
            return None


async def collect_tool_outputs(
    config: Config,
    session: Session,
    items: list[Any],
) -> list[dict[str, Any]]:
    tasks = [
        asyncio.to_thread(tool_output, config, session, item)
        for item in items
        if isinstance(item, dict) and needs_tool_output(item)
    ]
    return [output for output in await asyncio.gather(*tasks) if isinstance(output, dict)]


def request_args(
    config: Config,
    session: Session,
    items: list[Any],
    instructions: str,
) -> dict[str, Any]:
    return {
        "model": config.openai_model,
        "input": sanitize_input(items),  # type: ignore[arg-type]
        "instructions": instructions,
        "reasoning": reasoning_config(config),
        "store": False,
        "parallel_tool_calls": True,
        "include": ["reasoning.encrypted_content", "web_search_call.action.sources"],
        "context_management": [{"type": "compaction", "compact_threshold": COMPACT_THRESHOLD}],
        "tools": tools(),  # type: ignore[arg-type]
    }


def build_reply_result(
    response: Any,
    instructions: str,
    outputs: list[dict[str, Any]],
    response_outputs: list[dict[str, Any]] | None = None,
) -> ReplyResult:
    text = response_text(response, response_outputs)
    return {
        "text": text or "I couldn't generate a reply just now.",
        "output_items": outputs,
        "usage": usage_dict(response),
        "instructions": instructions,
    }


async def emit_text_delta(callback: Callable[[str], Any] | None, delta: str) -> None:
    if not callback or not delta:
        return
    result = callback(delta)
    if inspect.isawaitable(result):
        await result


async def emit_item(callback: Callable[[dict[str, Any]], Any] | None, item: dict[str, Any]) -> None:
    if not callback:
        return
    result = callback(item)
    if inspect.isawaitable(result):
        await result


async def emit_event(callback: Callable[[], Any] | None) -> None:
    if not callback:
        return
    result = callback()
    if inspect.isawaitable(result):
        await result


async def resolve_reply(
    config: Config,
    session: Session,
    messages: list[Any],
    instructions: str,
    request: Callable[[list[Any]], Any],
    on_stream_end: Callable[[list[dict[str, Any]], str], Any] | None = None,
) -> ReplyResult:
    items = list(messages)
    outputs: list[dict[str, Any]] = []
    while True:
        response = await request(items)
        response_outputs = [item for item in normalized_items(response.output) if isinstance(item, dict)]
        outputs.extend(response_outputs)
        items = compacted_items([*items, *response_outputs])
        next_items = await collect_tool_outputs(config, session, response_outputs)
        outputs.extend(next_items)
        items.extend(next_items)
        text = response_text(response, response_outputs)
        if on_stream_end and (outputs or text):
            result = on_stream_end(list(outputs), text)
            if inspect.isawaitable(result):
                await result
        if not next_items:
            return build_reply_result(response, instructions, outputs, response_outputs)


async def reply(
    openai_client: AsyncOpenAI,
    config: Config,
    session: Session,
    messages: list[Any],
) -> ReplyResult:
    instructions = system_instructions(config, session)
    return await resolve_reply(
        config,
        session,
        messages,
        instructions,
        lambda items: openai_client.responses.create(**request_args(config, session, items, instructions)),
    )


async def stream_reply(
    openai_client: AsyncOpenAI,
    config: Config,
    session: Session,
    messages: list[Any],
    on_text_delta: Callable[[str], Any] | None = None,
    on_reasoning_delta: Callable[[str], Any] | None = None,
    on_reasoning_done: Callable[[], Any] | None = None,
    on_output_item: Callable[[dict[str, Any]], Any] | None = None,
    on_stream_end: Callable[[list[dict[str, Any]], str], Any] | None = None,
) -> ReplyResult:
    instructions = system_instructions(config, session)

    async def stream_request(items: list[Any]) -> Any:
        async with openai_client.responses.stream(
            **request_args(config, session, items, instructions)
        ) as stream:
            async for event in stream:
                event_type = getattr(event, "type", None)
                if event_type == "response.output_item.done":
                    item = dict_item(getattr(event, "item", None).to_dict() if hasattr(getattr(event, "item", None), "to_dict") else getattr(event, "item", None))
                    if item is not None:
                        await emit_item(on_output_item, item)
                elif event_type == "response.output_text.delta":
                    await emit_text_delta(on_text_delta, getattr(event, "delta", ""))
                elif event_type == "response.reasoning_summary_text.delta":
                    await emit_text_delta(on_reasoning_delta, getattr(event, "delta", ""))
                elif event_type == "response.reasoning_summary_text.done":
                    await emit_event(on_reasoning_done)
            return await stream.get_final_response()

    return await resolve_reply(
        config,
        session,
        messages,
        instructions,
        stream_request,
        on_stream_end=on_stream_end,
    )
