import asyncio
import json
import os
import subprocess
import tempfile
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, TypeAlias, cast

import yaml
from pydantic import BaseModel

from faltoobot.config import app_root, build_config
from faltoobot.faltoochat.git import run_git_async
from faltoobot.gpt_utils import (
    MessageHistory,
    get_openai_client,
    trim_input,
)
from faltoobot.openai_auth import uses_chatgpt_oauth

HOOKS_DIR = "hooks"
DEFAULT_MAX_ITERATIONS = 3
HOOK_REVIEW_CONTEXT_TOKEN_BUDGET = 100_000
HOOK_TRIGGER_INSTRUCTIONS = """You are a code reviewer selecting user-defined review hooks.
Decide which hooks should run based only on the supplied code diff and each hook's trigger condition."""


class HookDiffScope(StrEnum):
    ALL = "all"
    UNSTAGED = "unstaged"


@dataclass(frozen=True, slots=True)
class Snapshot:
    repo_root: Path
    tree: str


HookStatus: TypeAlias = Literal[
    "running", "skipped", "triggered", "feedback", "stopped"
]


@dataclass(frozen=True, slots=True)
class HookEvent:
    text: str  # E.g. "Refactor: hook triggered".
    hook_name: str
    status: HookStatus
    feedback: str | None = None
    type: Literal["faltoobot.post_response_hook"] = field(
        init=False, default="faltoobot.post_response_hook"
    )


@dataclass(frozen=True, slots=True)
class HookCheck:
    name: str
    trigger: str
    prompt: str
    model: str | None = None


class HookTriggerResponse(BaseModel):
    hooks_to_run: list[str]


async def _repo_root(workspace: Path) -> Path | None:
    result = await run_git_async(workspace, "rev-parse", "--show-toplevel")
    if result is None or result.returncode != 0:
        return None
    text = result.stdout.strip()
    return Path(text) if text else None


async def _checked_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = await run_git_async(repo_root, *args)
    if result is None:
        raise RuntimeError("Git executable not found.")
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return result


async def _checked_index_git(
    repo_root: Path, index_name: str, *args: str
) -> subprocess.CompletedProcess[str]:
    result = await run_git_async(
        repo_root,
        *args,
        env={**os.environ, "GIT_INDEX_FILE": index_name},
    )
    if result is None:
        raise RuntimeError("Git executable not found.")
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return result


async def _diff_trees(repo_root: Path, before_tree: str, after_tree: str) -> str:
    result = await run_git_async(repo_root, "diff", before_tree, after_tree)
    if result is None:
        raise RuntimeError("Git executable not found.")
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return result.stdout.strip()


async def _write_worktree_tree(repo_root: Path) -> str:
    # Git's well-known empty tree lets snapshots work before the first commit.
    empty_tree = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    fd, index_name = tempfile.mkstemp(prefix="faltoobot-hooks-index-")
    os.close(fd)
    Path(index_name).unlink(missing_ok=True)
    try:
        head = await run_git_async(repo_root, "rev-parse", "--verify", "HEAD")
        tree = "HEAD" if head is not None and head.returncode == 0 else empty_tree
        await _checked_index_git(repo_root, index_name, "read-tree", tree)
        await _checked_index_git(repo_root, index_name, "add", "-A")
        return (
            await _checked_index_git(repo_root, index_name, "write-tree")
        ).stdout.strip()
    finally:
        Path(index_name).unlink(missing_ok=True)


async def _hook_dirs(workspace: Path, hooks_dir: Path | None) -> list[Path]:
    if hooks_dir is not None:
        return [hooks_dir]
    directories = [app_root() / HOOKS_DIR]
    if repo_root := await _repo_root(workspace):
        directories.append(repo_root / ".faltoobot" / HOOKS_DIR)
    return directories


async def _load_hooks(
    workspace: Path, *, hooks_dir: Path | None = None
) -> list[HookCheck]:
    hooks: list[HookCheck] = []
    for directory in await _hook_dirs(workspace, hooks_dir):
        if not directory.exists():
            continue
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            if path.suffix not in {".yaml", ".yml"}:
                continue
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise TypeError(f"Hook file must contain a YAML list: {path}")
            hooks.extend(
                HookCheck(
                    name=item["name"].strip(),
                    trigger=item["trigger"].strip(),
                    prompt=item["prompt"].strip(),
                    model=item.get("model", "").strip() or None,
                )
                for item in payload
            )

    hook_names = [hook.name for hook in hooks]
    # Trigger responses identify hooks by name, so configured names must be unique.
    if len(hook_names) != len(set(hook_names)):
        raise ValueError("Hook names must be unique")
    return hooks


def _review_prompt(hook: HookCheck, diff_text: str) -> str:
    return f"""You are running a user-defined code-review hook against the supplied code changes.

Review hook instructions:
<hook_instructions>
{hook.prompt}
</hook_instructions>

Code changes to review:
<diff>
{diff_text}
</diff>

Review only issues covered by the hook instructions. Return concise, actionable feedback describing what should be changed.""".strip()


def _trigger_prompt(hooks: Sequence[HookCheck], diff_text: str) -> str:
    hook_lines = "\n\n".join(
        f"Hook name: {hook.name}\nTrigger condition:\n{hook.trigger}" for hook in hooks
    )
    return f"""This is a diff of some code changes:
<diff>
{diff_text}
</diff>

These are the user's code-review hooks:
<hooks>
{hook_lines}
</hooks>

Return the names of the hooks that should run.""".strip()


def _trim_history_for_hook(messages: MessageHistory) -> MessageHistory:
    """Keep recent history within budget without orphaned tool outputs."""
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


def _parsed_hook_trigger(response: Any) -> HookTriggerResponse:
    # Refusals and empty responses have no parsed structured output.
    if response.output_parsed is None:
        raise RuntimeError("Hook trigger returned no structured output")
    return response.output_parsed


async def _run_hook_trigger(
    hooks: Sequence[HookCheck],
    diff_text: str,
) -> HookTriggerResponse:
    config = build_config()
    client = get_openai_client(config)
    prompt = _trigger_prompt(hooks, diff_text)
    configured_names = {hook.name for hook in hooks}
    try:
        response = await client.responses.parse(
            model=config.hook_model,
            input=prompt,
            instructions=HOOK_TRIGGER_INSTRUCTIONS,
            store=False,
            text_format=HookTriggerResponse,
        )
        parsed = _parsed_hook_trigger(response)
        unknown_names = set(parsed.hooks_to_run) - configured_names
        if not unknown_names:
            return parsed

        correction = f"""These hook names do not exist: {", ".join(sorted(unknown_names))}.
Choose only from these configured hooks: {", ".join(sorted(configured_names))}.
Return a corrected response."""
        response = await client.responses.parse(
            model=config.hook_model,
            input=cast(
                Any,
                [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": parsed.model_dump_json()},
                    {"role": "user", "content": correction},
                ],
            ),
            instructions=HOOK_TRIGGER_INSTRUCTIONS,
            store=False,
            text_format=HookTriggerResponse,
        )
        parsed = _parsed_hook_trigger(response)
    finally:
        await client.close()

    unknown_names = set(parsed.hooks_to_run) - configured_names
    if unknown_names:
        raise RuntimeError(
            f"Hook trigger returned unknown hooks: {', '.join(sorted(unknown_names))}"
        )
    return parsed


async def _run_hook_review(
    hook: HookCheck,
    diff_text: str,
    *,
    messages: MessageHistory,
    instructions: str,
) -> str:
    config = build_config()
    client = get_openai_client(config)
    try:
        response = await client.responses.create(
            model=hook.model or config.hook_model,
            input=cast(
                Any,
                trim_input(
                    [
                        *_trim_history_for_hook(messages),
                        {
                            "type": "message",
                            "role": "user",
                            "content": _review_prompt(hook, diff_text),
                        },
                    ],
                    replace_unavailable_uploads=uses_chatgpt_oauth(config),
                ),
            ),
            instructions=instructions or None,
            store=False,
        )
    finally:
        await client.close()
    return response.output_text


def format_feedback(feedback: Sequence[tuple[str, str]]) -> str:
    sections = [f"### {hook_name}\n\n{text}" for hook_name, text in feedback]
    return """## Automated code-review hook feedback

The following feedback came from automated review hooks. Use your judgment: address relevant findings and ignore anything incorrect or inapplicable.

""" + "\n\n".join(sections)


def _hook_event(
    status: HookStatus,
    *,
    hook_name: str = "",
    feedback: str | None = None,
) -> HookEvent:
    if feedback is not None:
        text = format_feedback([(hook_name, feedback)])
    elif status == "running":
        text = f"Running post-response hook: {hook_name}"
    else:
        text = f"{hook_name}: hook {status}"
    return HookEvent(
        text=text,
        hook_name=hook_name,
        status=status,
        feedback=feedback,
    )


async def _diff_for_scope(workspace: Path, scope: HookDiffScope) -> str:
    repo_root = await _repo_root(workspace)
    if repo_root is None:
        return ""
    after_tree = await _write_worktree_tree(repo_root)
    if scope == HookDiffScope.UNSTAGED:
        before_tree = (await _checked_git(repo_root, "write-tree")).stdout.strip()
    else:
        head = await run_git_async(repo_root, "rev-parse", "--verify", "HEAD")
        if head is not None and head.returncode == 0:
            before_tree = (
                await _checked_git(repo_root, "rev-parse", "HEAD^{tree}")
            ).stdout.strip()
        else:
            # Git's well-known empty tree lets diffs work before the first commit.
            before_tree = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    return await _diff_trees(repo_root, before_tree, after_tree)


async def _diff_since(snapshot: Snapshot | None) -> str:
    if snapshot is None:
        return ""
    return await _diff_trees(
        snapshot.repo_root,
        snapshot.tree,
        await _write_worktree_tree(snapshot.repo_root),
    )


async def capture_snapshot(workspace: Path) -> Snapshot | None:
    repo_root = await _repo_root(workspace)
    if repo_root is None:
        return None
    return Snapshot(repo_root=repo_root, tree=await _write_worktree_tree(repo_root))


async def run_hooks(
    workspace: Path,
    against: Snapshot | HookDiffScope | None,
    *,
    messages: MessageHistory,
    instructions: str,
) -> AsyncIterator[HookEvent]:
    """Run matching hooks against a snapshot or selected Git changes."""
    if isinstance(against, HookDiffScope):
        diff_text = await _diff_for_scope(workspace, against)
    else:
        diff_text = await _diff_since(against)
    if not diff_text.strip():
        return

    hooks = await _load_hooks(workspace)
    if not hooks:
        return
    for hook in hooks:
        yield _hook_event("running", hook_name=hook.name)

    trigger_response = await _run_hook_trigger(hooks, diff_text)
    triggered_names = set(trigger_response.hooks_to_run)
    triggered_hooks: list[HookCheck] = []
    for hook in hooks:
        if hook.name not in triggered_names:
            yield _hook_event("skipped", hook_name=hook.name)
            continue
        yield _hook_event("triggered", hook_name=hook.name)
        triggered_hooks.append(hook)

    feedback_texts = await asyncio.gather(
        *(
            _run_hook_review(
                hook,
                diff_text,
                messages=messages,
                instructions=instructions,
            )
            for hook in triggered_hooks
        )
    )
    for hook, feedback in zip(triggered_hooks, feedback_texts, strict=True):
        # An empty review means the hook found nothing for the assistant to change.
        if feedback := feedback.strip():
            yield _hook_event("feedback", hook_name=hook.name, feedback=feedback)
