import asyncio
import json
import os
import subprocess
import tempfile
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import yaml
from openai import omit

from faltoobot.config import app_root, build_config
from faltoobot.faltoochat.git import run_git
from faltoobot.gpt_utils import (
    MessageHistory,
    StreamingReplyItem,
    get_openai_client,
    trim_input,
)
from faltoobot.openai_auth import uses_chatgpt_oauth

HOOKS_DIR = "hooks"
DEFAULT_MAX_ITERATIONS = 3
TRIGGER_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "name": "post_response_hook_trigger",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {"run": {"type": "boolean"}},
        "required": ["run"],
        "additionalProperties": False,
    },
}

REVIEW_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "name": "post_response_hook_review",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "changes_required": {"type": "boolean"},
            "feedback": {"type": "string"},
        },
        "required": ["changes_required", "feedback"],
        "additionalProperties": False,
    },
}


@dataclass(frozen=True, slots=True)
class Snapshot:
    repo_root: Path
    tree: str


@dataclass(frozen=True, slots=True)
class HookFeedback:
    hook_name: str
    feedback: str


@dataclass(frozen=True, slots=True)
class HookCheck:
    name: str
    trigger: str
    prompt: str
    model: str | None = None


def capture_snapshot(workspace: Path) -> Snapshot | None:
    repo_root = _repo_root(workspace)
    if repo_root is None:
        return None
    return Snapshot(repo_root=repo_root, tree=_write_worktree_tree(repo_root))


def diff_since(snapshot: Snapshot | None) -> str:
    if snapshot is None:
        return ""
    after_tree = _write_worktree_tree(snapshot.repo_root)
    result = run_git(snapshot.repo_root, "diff", snapshot.tree, after_tree)
    if result is None:
        raise RuntimeError("Git executable not found.")
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return result.stdout.strip()


def load_hooks(workspace: Path, *, hooks_dir: Path | None = None) -> list[HookCheck]:
    hooks: list[HookCheck] = []
    for directory in _hook_dirs(workspace, hooks_dir):
        if not directory.exists():
            continue
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            if path.suffix not in {".yaml", ".yml"}:
                continue
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise TypeError(f"Hook file must contain a YAML list: {path}")
            hooks.extend(_hook_from_yaml(item) for item in payload)
    return hooks


async def run_hook_events(
    workspace: Path,
    snapshot: Snapshot | None,
    *,
    hook_model: str,
    messages: MessageHistory,
    instructions: str,
) -> AsyncIterator[StreamingReplyItem]:
    diff_text = await asyncio.to_thread(diff_since, snapshot)
    if not diff_text.strip():
        return

    for hook in load_hooks(workspace):
        yield _hook_event("status", hook_name=hook.name, status="running")
        model = hook.model or hook_model
        trigger_response = await _run_hook_sub_agent(
            _trigger_prompt(hook, diff_text),
            model,
            response_format=TRIGGER_RESPONSE_FORMAT,
            messages=None,
            instructions="",
        )
        if not json.loads(trigger_response)["run"]:
            yield _hook_event("status", hook_name=hook.name, status="skipped")
            continue

        yield _hook_event("status", hook_name=hook.name, status="triggered")
        review_response = await _run_hook_sub_agent(
            _review_prompt(hook, diff_text),
            model,
            response_format=REVIEW_RESPONSE_FORMAT,
            messages=messages,
            instructions=instructions,
        )
        review = json.loads(review_response)
        if review["changes_required"]:
            feedback_item = HookFeedback(hook.name, review["feedback"].strip())
            feedback_items = [feedback_item]
            yield _hook_event(
                "feedback",
                feedback=format_feedback(feedback_items),
                feedback_items=feedback_items,
            )


def _hook_event(event_name: str, **values: object) -> StreamingReplyItem:
    return cast(
        StreamingReplyItem,
        SimpleNamespace(
            **{"type": f"faltoobot.post_response_hook.{event_name}"},
            **values,
        ),
    )


async def _run_hook_sub_agent(
    prompt: str,
    model: str | None,
    *,
    response_format: dict[str, Any] | None,
    messages: MessageHistory | None,
    instructions: str,
) -> str:
    config = build_config()
    hook_model = model or config.openai_model
    input_items = trim_input(
        [*(messages or []), {"type": "message", "role": "user", "content": prompt}],
        replace_unavailable_uploads=uses_chatgpt_oauth(config),
    )
    client = get_openai_client(config)
    output_text = ""
    try:
        stream = await client.responses.create(
            model=hook_model,
            input=cast(Any, input_items),
            instructions=instructions or None,
            store=False,
            stream=True,
            text=cast(Any, {"format": response_format}) if response_format else omit,
        )
        async with stream:
            async for event in stream:
                if event.type == "response.output_text.done":
                    output_text = str(getattr(event, "text", ""))
    finally:
        await client.close()
    return output_text


def format_feedback(feedback: Sequence[HookFeedback]) -> str:
    sections = [f"### {item.hook_name}\n\n{item.feedback}" for item in feedback]
    return "## Post-response hook feedback\n\n" + "\n\n".join(sections)


def _hook_dirs(workspace: Path, hooks_dir: Path | None) -> list[Path]:
    if hooks_dir is not None:
        return [hooks_dir]
    directories = [app_root() / HOOKS_DIR]
    if repo_root := _repo_root(workspace):
        directories.append(repo_root / ".faltoobot" / HOOKS_DIR)
    return directories


def _hook_from_yaml(item: Any) -> HookCheck:
    return HookCheck(
        name=item["name"].strip(),
        trigger=item["trigger"].strip(),
        prompt=item["prompt"].strip(),
        model=item.get("model", "").strip() or None,
    )


def _trigger_prompt(hook: HookCheck, diff_text: str) -> str:
    return f"""You are deciding whether a post-response hook should run.

Hook name: {hook.name}
Trigger condition:
{hook.trigger}

Incremental diff from the assistant's last response:
<diff>
{diff_text}
</diff>""".strip()


def _review_prompt(hook: HookCheck, diff_text: str) -> str:
    return f"""{hook.prompt}

Incremental diff from the assistant's last response:
<diff>
{diff_text}
</diff>

Set changes_required to false when no follow-up is needed.""".strip()


def _write_worktree_tree(repo_root: Path) -> str:
    # Git's well-known empty tree lets snapshots work before the first commit.
    empty_tree = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    fd, index_name = tempfile.mkstemp(prefix="faltoobot-hooks-index-")
    os.close(fd)
    Path(index_name).unlink(missing_ok=True)
    env = {"GIT_INDEX_FILE": index_name}
    try:
        if _has_head(repo_root):
            _checked_git(repo_root, "read-tree", "HEAD", env=env)
        else:
            _checked_git(repo_root, "read-tree", empty_tree, env=env)
        _checked_git(repo_root, "add", "-A", env=env)
        return _checked_git(repo_root, "write-tree", env=env).stdout.strip()
    finally:
        Path(index_name).unlink(missing_ok=True)


def _checked_git(
    repo_root: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    result = run_git(repo_root, *args, env=env)
    if result is None:
        raise RuntimeError("Git executable not found.")
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return result


def _has_head(repo_root: Path) -> bool:
    result = run_git(repo_root, "rev-parse", "--verify", "HEAD")
    return result is not None and result.returncode == 0


def _repo_root(workspace: Path) -> Path | None:
    result = run_git(workspace, "rev-parse", "--show-toplevel")
    if result is None or result.returncode != 0:
        return None
    text = result.stdout.strip()
    return Path(text) if text else None
