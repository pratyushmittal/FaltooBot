import asyncio
import json
import os
import subprocess
import tempfile
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
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
HOOK_REVIEW_CONTEXT_TOKEN_BUDGET = 100_000
TRIGGER_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "name": "post_response_hook_triggers",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "run": {"type": "boolean"},
                    },
                    "required": ["index", "run"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["results"],
        "additionalProperties": False,
    },
}


class HookDiffScope(StrEnum):
    ALL = "all"
    UNSTAGED = "unstaged"


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
    return _diff_trees(
        snapshot.repo_root, snapshot.tree, _write_worktree_tree(snapshot.repo_root)
    )


def diff_for_scope(workspace: Path, scope: HookDiffScope) -> str:
    repo_root = _repo_root(workspace)
    if repo_root is None:
        return ""
    after_tree = _write_worktree_tree(repo_root)
    if scope == HookDiffScope.UNSTAGED:
        before_tree = _checked_git(repo_root, "write-tree").stdout.strip()
    elif _has_head(repo_root):
        before_tree = _checked_git(repo_root, "rev-parse", "HEAD^{tree}").stdout.strip()
    else:
        # Git's well-known empty tree lets diffs work before the first commit.
        before_tree = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    return _diff_trees(repo_root, before_tree, after_tree)


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
    diff_text: str,
    *,
    messages: MessageHistory,
    instructions: str,
) -> AsyncIterator[StreamingReplyItem]:
    if not diff_text.strip():
        return

    hooks = load_hooks(workspace)
    if not hooks:
        return
    for hook in hooks:
        yield _hook_event("status", hook_name=hook.name, status="running")

    trigger_response = await _run_hook_sub_agent(
        _trigger_prompt(hooks, diff_text),
        None,
        response_format=TRIGGER_RESPONSE_FORMAT,
        messages=None,
        instructions="",
    )
    trigger_results = {
        int(item["index"]): bool(item["run"])
        for item in json.loads(trigger_response)["results"]
    }

    triggered_hooks: list[HookCheck] = []
    for index, hook in enumerate(hooks):
        if not trigger_results[index]:
            yield _hook_event("status", hook_name=hook.name, status="skipped")
            continue
        yield _hook_event("status", hook_name=hook.name, status="triggered")
        triggered_hooks.append(hook)

    feedback_texts = await asyncio.gather(
        *(
            _run_hook_sub_agent(
                _review_prompt(hook, diff_text),
                hook.model,
                response_format=None,
                messages=_recent_messages(messages),
                instructions=instructions,
            )
            for hook in triggered_hooks
        )
    )
    for hook, feedback_text in zip(triggered_hooks, feedback_texts, strict=True):
        feedback_item = HookFeedback(hook.name, feedback_text.strip())
        feedback_items = [feedback_item]
        yield _hook_event(
            "feedback",
            feedback=format_feedback(feedback_items),
            feedback_items=feedback_items,
        )


def _recent_messages(messages: MessageHistory) -> MessageHistory:
    char_budget = HOOK_REVIEW_CONTEXT_TOKEN_BUDGET * 4
    kept: MessageHistory = []
    total_chars = 0
    for message in reversed(messages):
        message_size = len(json.dumps(message, ensure_ascii=False))
        if total_chars + message_size > char_budget:
            break
        kept.append(message)
        total_chars += message_size
    trimmed = list(reversed(kept))
    # Tool call outputs without preceding tool calls cause OpenAI input errors,
    # so remove orphaned outputs from the trimmed transcript.
    while trimmed and trimmed[0].get("type") == "function_call_output":
        trimmed.pop(0)
    return trimmed


def _hook_event(event_name: str, **values: object) -> StreamingReplyItem:
    return cast(
        StreamingReplyItem,
        SimpleNamespace(type=f"faltoobot.post_response_hook.{event_name}", **values),
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
    hook_model = model or config.hook_model
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


def _trigger_prompt(hooks: Sequence[HookCheck], diff_text: str) -> str:
    hook_lines = "\n\n".join(
        f"Hook index: {index}\nHook name: {hook.name}\nTrigger condition:\n{hook.trigger}"
        for index, hook in enumerate(hooks)
    )
    return f"""You are deciding which post-response hooks should run.
Return one result for each hook index.

{hook_lines}

Incremental diff from the assistant's last response:
<diff>
{diff_text}
</diff>""".strip()


def _review_prompt(hook: HookCheck, diff_text: str) -> str:
    return f"""{hook.prompt}

Incremental diff from the assistant's last response:
<diff>
{diff_text}
</diff>""".strip()


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


def _diff_trees(repo_root: Path, before_tree: str, after_tree: str) -> str:
    result = run_git(repo_root, "diff", before_tree, after_tree)
    if result is None:
        raise RuntimeError("Git executable not found.")
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return result.stdout.strip()


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
