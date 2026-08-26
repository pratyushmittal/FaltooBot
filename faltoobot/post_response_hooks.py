import os
import subprocess
import tempfile
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, TypeAlias

import yaml
from pydantic import BaseModel

from faltoobot.config import app_root, build_config
from faltoobot.faltoochat.git import run_git_async
from faltoobot.gpt_utils import get_openai_client

HOOKS_DIR = "hooks"
DEFAULT_MAX_ITERATIONS = 3
HOOK_TRIGGER_MODEL = "gpt-5.6-luna"
HOOK_TRIGGER_INSTRUCTIONS = """You are a code reviewer selecting user-defined review hooks.
Decide which hooks should run based only on the supplied code diff and each hook's trigger condition."""


class HookDiffScope(StrEnum):
    ALL = "all"
    UNSTAGED = "unstaged"


@dataclass(frozen=True, slots=True)
class Snapshot:
    repo_root: Path
    tree: str
    branch: str


HookStatus: TypeAlias = Literal["running", "skipped", "triggered", "stopped"]


@dataclass(frozen=True, slots=True)
class HookEvent:
    text: str  # E.g. "Refactor: hook triggered".
    hook_name: str
    status: HookStatus
    prompt: str | None = None  # Configured prompt when the hook is triggered.
    type: Literal["faltoobot.post_response_hook"] = field(
        init=False, default="faltoobot.post_response_hook"
    )


@dataclass(frozen=True, slots=True)
class HookCheck:
    name: str
    trigger: str
    prompt: str


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
                )
                for item in payload
            )

    hook_names = [hook.name for hook in hooks]
    # Trigger responses identify hooks by name, so configured names must be unique.
    if len(hook_names) != len(set(hook_names)):
        raise ValueError("Hook names must be unique")
    return hooks


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


async def _request_hook_trigger(
    client: Any, input_items: list[dict[str, str]]
) -> HookTriggerResponse:
    response_text = ""
    async with client.responses.stream(
        model=HOOK_TRIGGER_MODEL,
        input=input_items,
        instructions=HOOK_TRIGGER_INSTRUCTIONS,
        store=False,
        text_format=HookTriggerResponse,
    ) as stream:
        async for event in stream:
            # Codex OAuth streams do not populate the SDK's output_parsed field.
            if event.type == "response.output_text.done":
                response_text = event.text
    # Refusals and interrupted streams can finish without output text.
    if not response_text:
        raise RuntimeError("Hook trigger returned no structured output")
    return HookTriggerResponse.model_validate_json(response_text)


async def _run_hook_trigger(
    hooks: Sequence[HookCheck],
    diff_text: str,
) -> HookTriggerResponse:
    config = build_config()
    client = get_openai_client(config)
    prompt = _trigger_prompt(hooks, diff_text)
    configured_names = {hook.name for hook in hooks}
    try:
        parsed = await _request_hook_trigger(
            client,
            [{"role": "user", "content": prompt}],
        )
        unknown_names = set(parsed.hooks_to_run) - configured_names
        if not unknown_names:
            return parsed

        correction = f"""These hook names do not exist: {", ".join(sorted(unknown_names))}.
Choose only from these configured hooks: {", ".join(sorted(configured_names))}.
Return a corrected response."""
        parsed = await _request_hook_trigger(
            client,
            [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": parsed.model_dump_json()},
                {"role": "user", "content": correction},
            ],
        )
    finally:
        await client.close()

    unknown_names = set(parsed.hooks_to_run) - configured_names
    if unknown_names:
        raise RuntimeError(
            f"Hook trigger returned unknown hooks: {', '.join(sorted(unknown_names))}"
        )
    return parsed


def _hook_event(
    status: HookStatus,
    *,
    hook_name: str = "",
    prompt: str | None = None,
) -> HookEvent:
    if status == "running":
        text = f"Running post-response hook: {hook_name}"
    else:
        text = f"{hook_name}: hook {status}"
    return HookEvent(
        text=text,
        hook_name=hook_name,
        status=status,
        prompt=prompt,
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
    branch = (
        await _checked_git(snapshot.repo_root, "branch", "--show-current")
    ).stdout.strip()
    if branch != snapshot.branch:
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
    branch = (await _checked_git(repo_root, "branch", "--show-current")).stdout.strip()
    return Snapshot(
        repo_root=repo_root,
        tree=await _write_worktree_tree(repo_root),
        branch=branch,
    )


async def run_hooks(
    workspace: Path,
    against: Snapshot | HookDiffScope | None,
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
    for hook in hooks:
        if hook.name not in triggered_names:
            yield _hook_event("skipped", hook_name=hook.name)
            continue
        yield _hook_event("triggered", hook_name=hook.name, prompt=hook.prompt)
