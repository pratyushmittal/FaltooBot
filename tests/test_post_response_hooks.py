import asyncio
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from faltoobot import post_response_hooks, sessions

scenarios("features/post_response_hooks.feature")


def git(workspace: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=workspace, capture_output=True, text=True, check=True
    )
    return result.stdout


def init_repo(workspace: Path) -> None:
    workspace.mkdir()
    git(workspace, "init")
    git(workspace, "config", "user.email", "tests@example.com")
    git(workspace, "config", "user.name", "Tests")


@dataclass
class HookBDD:
    tmp_path: Path
    workspace: Path | None = None
    hooks_dir: Path | None = None
    snapshot: post_response_hooks.Snapshot | None = None
    diff: str = ""
    hooks: list[post_response_hooks.HookCheck] = field(default_factory=list)
    trigger_output: str = '{"hooks_to_run": []}'
    calls: list[tuple[str, str | None, str]] = field(default_factory=list)
    seen_messages: list[list[dict[str, Any]] | None] = field(default_factory=list)
    seen_instructions: list[str] = field(default_factory=list)
    captured_response_call: dict[str, object] = field(default_factory=dict)
    parsed_response_calls: list[dict[str, Any]] = field(default_factory=list)
    unknown_trigger_once: bool = False
    session: sessions.Session | None = None
    events: list[Any] = field(default_factory=list)
    stream_calls: int = 0
    with_transcript_context: bool = False


@pytest.fixture
def hook_bdd(tmp_path: Path) -> HookBDD:
    return HookBDD(tmp_path=tmp_path)


def _workspace(ctx: HookBDD) -> Path:
    if ctx.workspace is None:
        raise AssertionError("workspace has not been created")
    return ctx.workspace


def _hooks_dir(ctx: HookBDD) -> Path:
    if ctx.hooks_dir is None:
        raise AssertionError("hooks dir has not been created")
    return ctx.hooks_dir


def _session(ctx: HookBDD) -> sessions.Session:
    if ctx.session is None:
        raise AssertionError("session has not been created")
    return ctx.session


def _write_hook(ctx: HookBDD, *, name: str, model: str) -> None:
    workspace = ctx.tmp_path / "workspace"
    init_repo(workspace)
    (workspace / "page.html").write_text("<main>before</main>\n", encoding="utf-8")
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "initial")
    ctx.workspace = workspace

    ctx.hooks_dir = workspace / ".faltoobot" / "hooks"
    ctx.hooks_dir.mkdir(parents=True, exist_ok=True)
    model_line = f"  model: {model}\n" if model else ""
    (ctx.hooks_dir / "review.yaml").write_text(
        f"- name: {name}\n{model_line}  trigger: HTML changed\n  prompt: Review HTML.\n",
        encoding="utf-8",
    )


def _hook_statuses(events: list[Any]) -> list[str]:
    return [
        event.status
        for event in events
        if event.type == "faltoobot.post_response_hook" and event.feedback is None
    ]


def _hook_feedback_items(events: list[Any]) -> list[tuple[str, str]]:
    return [
        (event.hook_name, event.feedback)
        for event in events
        if event.type == "faltoobot.post_response_hook" and event.feedback is not None
    ]


@given("a git workspace with an initial page")
def git_workspace_with_initial_page(hook_bdd: HookBDD) -> None:
    workspace = hook_bdd.tmp_path / "workspace"
    init_repo(workspace)
    (workspace / "page.html").write_text("<main>before</main>\n", encoding="utf-8")
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "initial")
    hook_bdd.workspace = workspace


@given("a git workspace with no commits")
def git_workspace_with_no_commits(hook_bdd: HookBDD) -> None:
    workspace = hook_bdd.tmp_path / "workspace"
    workspace.mkdir()
    git(workspace, "init")
    hook_bdd.workspace = workspace


@given("the user changed the page before the snapshot")
def user_changed_page_before_snapshot(hook_bdd: HookBDD) -> None:
    workspace = _workspace(hook_bdd)
    (workspace / "page.html").write_text("<main>user change</main>\n", encoding="utf-8")
    (workspace / "preexisting.txt").write_text("already here\n", encoding="utf-8")


@when("I capture a post-response snapshot")
def capture_post_response_snapshot(hook_bdd: HookBDD) -> None:
    hook_bdd.snapshot = asyncio.run(
        post_response_hooks.capture_snapshot(_workspace(hook_bdd))
    )


@when("the assistant changes the page and creates a new file")
def assistant_changes_page_and_new_file(hook_bdd: HookBDD) -> None:
    workspace = _workspace(hook_bdd)
    (workspace / "page.html").write_text(
        "<main>agent change</main>\n", encoding="utf-8"
    )
    (workspace / "new.txt").write_text("new file\n", encoding="utf-8")
    hook_bdd.diff = asyncio.run(post_response_hooks._diff_since(hook_bdd.snapshot))


@when("the assistant creates a new file")
def assistant_creates_new_file(hook_bdd: HookBDD) -> None:
    (_workspace(hook_bdd) / "new.txt").write_text("new file\n", encoding="utf-8")
    hook_bdd.diff = asyncio.run(post_response_hooks._diff_since(hook_bdd.snapshot))


@then("the incremental diff contains only the assistant changes")
def incremental_diff_contains_only_assistant_changes(hook_bdd: HookBDD) -> None:
    assert "user change" in hook_bdd.diff
    assert "agent change" in hook_bdd.diff
    assert "new file" in hook_bdd.diff
    assert "preexisting.txt" not in hook_bdd.diff
    assert "<main>before</main>" not in hook_bdd.diff


@then("the incremental diff contains the new file")
def incremental_diff_contains_new_file(hook_bdd: HookBDD) -> None:
    assert "new.txt" in hook_bdd.diff
    assert "new file" in hook_bdd.diff


@given("global and project hook files")
def global_and_project_hook_files(
    hook_bdd: HookBDD, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = hook_bdd.tmp_path / "workspace"
    init_repo(workspace)
    global_hooks = hook_bdd.tmp_path / "home" / "hooks"
    project_hooks = workspace / ".faltoobot" / "hooks"
    global_hooks.mkdir(parents=True)
    project_hooks.mkdir(parents=True)
    (global_hooks / "global.yaml").write_text(
        "- name: Global\n  trigger: any diff\n  prompt: Review.\n", encoding="utf-8"
    )
    (project_hooks / "project.yaml").write_text(
        "- name: Project\n  trigger: any diff\n  prompt: Review.\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        post_response_hooks, "app_root", lambda: hook_bdd.tmp_path / "home"
    )
    hook_bdd.workspace = workspace


@when("I load hooks for the workspace")
def load_hooks_for_workspace(hook_bdd: HookBDD) -> None:
    hook_bdd.hooks = asyncio.run(post_response_hooks._load_hooks(_workspace(hook_bdd)))


@then(parsers.parse('the loaded hooks are "{names}"'))
def loaded_hooks_are(hook_bdd: HookBDD, names: str) -> None:
    assert [hook.name for hook in hook_bdd.hooks] == names.split(",")


@given(parsers.parse('a hook file named "{name}" with model "{model}"'))
def hook_file_named_with_model(hook_bdd: HookBDD, name: str, model: str) -> None:
    _write_hook(hook_bdd, name=name, model=model)


@given("two hook files")
def two_hook_files(hook_bdd: HookBDD) -> None:
    _write_hook(hook_bdd, name="Check HTML", model="hook-model")
    (_hooks_dir(hook_bdd) / "second.yaml").write_text(
        "- name: Check CSS\n"
        "  model: hook-model\n"
        "  trigger: CSS changed\n"
        "  prompt: Review CSS.\n",
        encoding="utf-8",
    )


@given("the structured hook trigger selects only the second hook")
def structured_hook_trigger_selects_only_second(hook_bdd: HookBDD) -> None:
    hook_bdd.trigger_output = '{"hooks_to_run": ["Check CSS"]}'


@given("the structured hook trigger selects the hook")
def structured_hook_trigger_selects_hook(hook_bdd: HookBDD) -> None:
    hook_bdd.trigger_output = '{"hooks_to_run": ["Check HTML"]}'


@given("the structured hook trigger selects no hooks")
def structured_hook_trigger_selects_no_hooks(hook_bdd: HookBDD) -> None:
    hook_bdd.trigger_output = '{"hooks_to_run": []}'


@given("transcript context exists")
def transcript_context_exists(hook_bdd: HookBDD) -> None:
    hook_bdd.with_transcript_context = True


@when("I run the hook")
def run_the_hook(hook_bdd: HookBDD, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        post_response_hooks,
        "build_config",
        lambda: SimpleNamespace(hook_model="config-model"),
    )
    monkeypatch.setattr(
        post_response_hooks, "app_root", lambda: hook_bdd.tmp_path / "home"
    )

    async def fake_hook_trigger(
        hooks: list[post_response_hooks.HookCheck],
        diff_text: str,
    ) -> post_response_hooks.HookTriggerResponse:
        hook_bdd.calls.append(
            (post_response_hooks._trigger_prompt(hooks, diff_text), None, "trigger")
        )
        return post_response_hooks.HookTriggerResponse.model_validate_json(
            hook_bdd.trigger_output
        )

    async def fake_hook_review(
        hook: post_response_hooks.HookCheck,
        diff_text: str,
        *,
        messages: list[dict[str, Any]],
        instructions: str,
    ) -> str:
        hook_bdd.calls.append(
            (post_response_hooks._review_prompt(hook, diff_text), hook.model, "review")
        )
        hook_bdd.seen_messages.append(messages)
        hook_bdd.seen_instructions.append(instructions)
        return "feedback"

    monkeypatch.setattr(post_response_hooks, "_run_hook_trigger", fake_hook_trigger)
    monkeypatch.setattr(post_response_hooks, "_run_hook_review", fake_hook_review)
    workspace = _workspace(hook_bdd)
    snapshot = asyncio.run(post_response_hooks.capture_snapshot(workspace))
    (workspace / "page.html").write_text("<main>html diff</main>\n", encoding="utf-8")
    kwargs: dict[str, Any] = {
        "messages": [],
        "instructions": "",
    }
    if hook_bdd.with_transcript_context:
        kwargs["messages"] = [{"role": "assistant", "content": "existing context"}]
        kwargs["instructions"] = "system prompt"

    async def collect_events() -> list[Any]:
        return [
            event
            async for event in post_response_hooks.run_hooks(
                workspace, snapshot, **kwargs
            )
        ]

    hook_bdd.events = asyncio.run(collect_events())


@then("the hook is triggered")
def hook_is_triggered(hook_bdd: HookBDD) -> None:
    assert _hook_statuses(hook_bdd.events) == ["running", "triggered"]


@then("the hook is skipped")
def hook_is_skipped(hook_bdd: HookBDD) -> None:
    assert _hook_statuses(hook_bdd.events) == ["running", "skipped"]


@then(parsers.parse('the hook feedback is "{feedback}"'))
def hook_feedback_is(hook_bdd: HookBDD, feedback: str) -> None:
    assert _hook_feedback_items(hook_bdd.events) == [("Check HTML", feedback)]


@then("only the trigger uses a structured response format")
def only_trigger_uses_structured_format(hook_bdd: HookBDD) -> None:
    assert [call[1] for call in hook_bdd.calls] == [None, "hook-model"]
    assert [call[2] for call in hook_bdd.calls] == ["trigger", "review"]


@then("batched hook statuses show one skipped and one triggered")
def batched_hook_statuses_show_one_skipped_and_one_triggered(hook_bdd: HookBDD) -> None:
    assert _hook_statuses(hook_bdd.events) == [
        "running",
        "running",
        "skipped",
        "triggered",
    ]


@then("trigger is called once")
def trigger_is_called_once(hook_bdd: HookBDD) -> None:
    trigger_calls = [call for call in hook_bdd.calls if call[2] == "trigger"]
    assert len(trigger_calls) == 1
    assert trigger_calls[0][1] is None


@then("review is not called")
def review_is_not_called(hook_bdd: HookBDD) -> None:
    assert len(hook_bdd.calls) == 1
    assert hook_bdd.calls[0][2] == "trigger"


@then("review receives the transcript context")
def review_receives_transcript_context(hook_bdd: HookBDD) -> None:
    assert _hook_statuses(hook_bdd.events) == ["running", "triggered"]
    assert "Trigger condition" in hook_bdd.calls[0][0]
    assert (
        "<hook_instructions>\nReview HTML.\n</hook_instructions>"
        in hook_bdd.calls[1][0]
    )
    assert "Code changes to review" in hook_bdd.calls[1][0]
    assert "html diff" in hook_bdd.calls[1][0]
    assert hook_bdd.seen_messages == [
        [{"role": "assistant", "content": "existing context"}]
    ]
    assert hook_bdd.seen_instructions == ["system prompt"]


@given("a fake parsed Responses client")
def fake_parsed_responses_client(
    hook_bdd: HookBDD, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeResponses:
        async def parse(self, **kwargs):
            hook_bdd.captured_response_call.update(kwargs)
            hook_bdd.parsed_response_calls.append(kwargs)
            hook_name = (
                "Unknown hook"
                if hook_bdd.unknown_trigger_once
                and len(hook_bdd.parsed_response_calls) == 1
                else "Check HTML"
            )
            return SimpleNamespace(
                output_parsed=post_response_hooks.HookTriggerResponse(
                    hooks_to_run=[hook_name]
                )
            )

    class FakeClient:
        responses = FakeResponses()

        async def close(self) -> None:
            hook_bdd.captured_response_call["closed"] = True

    monkeypatch.setattr(
        post_response_hooks,
        "build_config",
        lambda: SimpleNamespace(hook_model="config-model", openai_oauth=""),
    )
    monkeypatch.setattr(
        post_response_hooks, "get_openai_client", lambda _config: FakeClient()
    )


@given("the parsed trigger returns an unknown hook first")
def parsed_trigger_returns_unknown_hook_first(hook_bdd: HookBDD) -> None:
    hook_bdd.unknown_trigger_once = True


@when("the structured hook trigger runs")
def structured_hook_trigger_runs(hook_bdd: HookBDD) -> None:
    hook_bdd.events = [
        asyncio.run(
            post_response_hooks._run_hook_trigger(
                [post_response_hooks.HookCheck("Check HTML", "HTML changed", "Review")],
                "diff",
            )
        )
    ]


@then("it requests parsed structured Responses input")
def requests_parsed_structured_responses_input(hook_bdd: HookBDD) -> None:
    assert hook_bdd.events == [
        post_response_hooks.HookTriggerResponse(hooks_to_run=["Check HTML"])
    ]
    assert hook_bdd.captured_response_call["model"] == "config-model"
    prompt = str(hook_bdd.captured_response_call["input"])
    assert "Check HTML" in prompt
    assert "HTML changed" in prompt
    assert "diff" in prompt
    assert (
        hook_bdd.captured_response_call["instructions"]
        == post_response_hooks.HOOK_TRIGGER_INSTRUCTIONS
    )
    assert (
        hook_bdd.captured_response_call["text_format"]
        is post_response_hooks.HookTriggerResponse
    )
    assert hook_bdd.captured_response_call["closed"] is True


@then("it retries with a correction for the unknown hook")
def retries_with_unknown_hook_correction(hook_bdd: HookBDD) -> None:
    _, retry_call = hook_bdd.parsed_response_calls
    retry_input = retry_call["input"]
    assert isinstance(retry_input, list)
    correction = retry_input[-1]
    assert isinstance(correction, dict)
    assert "Unknown hook" in str(correction["content"])
    assert "Check HTML" in str(correction["content"])


@given("a hook-enabled session with one hook")
def hook_enabled_session_with_one_hook(
    hook_bdd: HookBDD, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sessions, "app_root", lambda: hook_bdd.tmp_path / ".faltoobot")
    monkeypatch.setattr(
        sessions,
        "build_config",
        lambda: SimpleNamespace(
            root=hook_bdd.tmp_path,
            bot_name="Faltoo",
            hook_enabled=True,
        ),
    )
    monkeypatch.setattr(
        sessions,
        "get_system_instructions",
        lambda *_args: "system prompt",
    )
    session = sessions.get_session(
        chat_key=sessions.get_dir_chat_key(hook_bdd.tmp_path / "workspace"),
        workspace=hook_bdd.tmp_path / "workspace",
    )
    hook_bdd.session = session

    async def fake_capture_snapshot(_workspace: Path) -> object:
        return object()

    async def fake_load_hooks(_workspace: Path) -> list[post_response_hooks.HookCheck]:
        return [post_response_hooks.HookCheck("Refactor", "any", "fix")]

    monkeypatch.setattr(post_response_hooks, "capture_snapshot", fake_capture_snapshot)
    monkeypatch.setattr(post_response_hooks, "_load_hooks", fake_load_hooks)


@given(parsers.parse('the incremental diff is "{diff}"'))
def incremental_diff_is(
    hook_bdd: HookBDD, monkeypatch: pytest.MonkeyPatch, diff: str
) -> None:
    async def fake_diff_since(_snapshot: object) -> str:
        return diff

    monkeypatch.setattr(post_response_hooks, "_diff_since", fake_diff_since)


@given("the hook run result is skipped")
def hook_run_result_is_skipped(
    hook_bdd: HookBDD, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_hook_trigger(
        _hooks: list[post_response_hooks.HookCheck],
        _diff_text: str,
    ) -> post_response_hooks.HookTriggerResponse:
        return post_response_hooks.HookTriggerResponse(hooks_to_run=[])

    monkeypatch.setattr(post_response_hooks, "_run_hook_trigger", fake_hook_trigger)


@given(parsers.parse('the hook run result has feedback "{feedback}"'))
def hook_run_result_has_feedback(
    hook_bdd: HookBDD, monkeypatch: pytest.MonkeyPatch, feedback: str
) -> None:
    hook_batches = iter([[post_response_hooks.HookCheck("Refactor", "any", "fix")], []])

    async def fake_load_hooks(_workspace: Path) -> list[post_response_hooks.HookCheck]:
        return next(hook_batches)

    monkeypatch.setattr(post_response_hooks, "_load_hooks", fake_load_hooks)

    async def fake_hook_trigger(
        _hooks: list[post_response_hooks.HookCheck],
        _diff_text: str,
    ) -> post_response_hooks.HookTriggerResponse:
        return post_response_hooks.HookTriggerResponse(hooks_to_run=["Refactor"])

    async def fake_hook_review(
        _hook: post_response_hooks.HookCheck,
        _diff_text: str,
        *,
        messages: list[dict[str, Any]],
        instructions: str,
    ) -> str:
        assert messages is not None
        assert instructions == "system prompt"
        return feedback

    monkeypatch.setattr(post_response_hooks, "_run_hook_trigger", fake_hook_trigger)
    monkeypatch.setattr(post_response_hooks, "_run_hook_review", fake_hook_review)


@given("the hook review returns no feedback")
def hook_review_returns_no_feedback(
    hook_bdd: HookBDD, monkeypatch: pytest.MonkeyPatch
) -> None:
    hook_run_result_has_feedback(hook_bdd, monkeypatch, "")


@when("the assistant answer is streamed")
def assistant_answer_is_streamed(
    hook_bdd: HookBDD, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_streaming_reply(**_kwargs):
        hook_bdd.stream_calls += 1
        yield SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(output=[], output_text=""),
        )

    monkeypatch.setattr(sessions, "_get_streaming_reply", fake_streaming_reply)

    async def collect_events() -> list[Any]:
        return [
            event
            async for event in sessions.get_answer_streaming_with_hooks(
                _session(hook_bdd)
            )
        ]

    hook_bdd.events = asyncio.run(collect_events())


@then("hook status events are running and skipped")
def hook_status_events_are_running_and_skipped(hook_bdd: HookBDD) -> None:
    assert [event.type for event in hook_bdd.events] == [
        "response.completed",
        "faltoobot.post_response_hook",
        "faltoobot.post_response_hook",
    ]
    assert hook_bdd.events[1].status == "running"
    assert hook_bdd.events[2].hook_name == "Refactor"
    assert hook_bdd.events[2].status == "skipped"


@then("the assistant is rerun after hook feedback")
def assistant_is_rerun_after_hook_feedback(hook_bdd: HookBDD) -> None:
    assert [event.type for event in hook_bdd.events] == [
        "response.completed",
        "faltoobot.post_response_hook",
        "faltoobot.post_response_hook",
        "faltoobot.post_response_hook",
        "response.completed",
    ]
    assert hook_bdd.events[1].hook_name == "Refactor"
    assert hook_bdd.events[1].status == "running"
    assert hook_bdd.events[2].status == "triggered"
    assert hook_bdd.events[3].text == (
        "## Automated code-review hook feedback\n\n"
        "The following feedback came from automated review hooks. Use your judgment: "
        "address relevant findings and ignore anything incorrect or inapplicable.\n\n"
        "### Refactor\n\nfix it"
    )
    assert hook_bdd.stream_calls == len(["initial", "follow-up"])
    assert (
        sessions.get_messages(_session(hook_bdd))["messages"][-1]["role"] == "developer"
    )


@then("the assistant is not rerun")
def assistant_is_not_rerun(hook_bdd: HookBDD) -> None:
    assert [event.type for event in hook_bdd.events] == [
        "response.completed",
        "faltoobot.post_response_hook",
        "faltoobot.post_response_hook",
    ]
    assert hook_bdd.events[-1].status == "triggered"
    assert hook_bdd.stream_calls == 1
    assert all(
        message.get("role") != "developer"
        for message in sessions.get_messages(_session(hook_bdd))["messages"]
    )


@given("a git workspace with staged and unstaged changes")
def git_workspace_with_staged_and_unstaged_changes(hook_bdd: HookBDD) -> None:
    workspace = hook_bdd.tmp_path / "workspace"
    init_repo(workspace)
    (workspace / "page.html").write_text("<main>before</main>\n", encoding="utf-8")
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "initial")
    (workspace / "cached-change.txt").write_text("staged\n", encoding="utf-8")
    git(workspace, "add", "cached-change.txt")
    (workspace / "worktree-change.txt").write_text("unstaged\n", encoding="utf-8")
    hook_bdd.workspace = workspace


@when(parsers.parse('I build the hook diff for "{scope}"'))
def build_hook_diff_for_scope(hook_bdd: HookBDD, scope: str) -> None:
    hook_bdd.diff = asyncio.run(
        post_response_hooks._diff_for_scope(
            _workspace(hook_bdd), post_response_hooks.HookDiffScope(scope)
        )
    )


@then(parsers.parse('the hook diff contains "{text}"'))
def hook_diff_contains(hook_bdd: HookBDD, text: str) -> None:
    assert text in hook_bdd.diff


@then(parsers.parse('the hook diff does not contain "{text}"'))
def hook_diff_does_not_contain(hook_bdd: HookBDD, text: str) -> None:
    assert text not in hook_bdd.diff
