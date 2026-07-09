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
    trigger_output: str = '{"run": true}'
    calls: list[tuple[str, str | None, dict[str, Any] | None]] = field(
        default_factory=list
    )
    seen_messages: list[list[dict[str, Any]] | None] = field(default_factory=list)
    seen_instructions: list[str] = field(default_factory=list)
    captured_response_call: dict[str, object] = field(default_factory=dict)
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
        if event.type == "faltoobot.post_response_hook.status"
    ]


def _hook_feedback_items(events: list[Any]) -> list[post_response_hooks.HookFeedback]:
    items = []
    for event in events:
        if event.type == "faltoobot.post_response_hook.feedback":
            items.extend(event.feedback_items)
    return items


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
    hook_bdd.snapshot = post_response_hooks.capture_snapshot(_workspace(hook_bdd))


@when("the assistant changes the page and creates a new file")
def assistant_changes_page_and_new_file(hook_bdd: HookBDD) -> None:
    workspace = _workspace(hook_bdd)
    (workspace / "page.html").write_text(
        "<main>agent change</main>\n", encoding="utf-8"
    )
    (workspace / "new.txt").write_text("new file\n", encoding="utf-8")
    hook_bdd.diff = post_response_hooks.diff_since(hook_bdd.snapshot)


@when("the assistant creates a new file")
def assistant_creates_new_file(hook_bdd: HookBDD) -> None:
    (_workspace(hook_bdd) / "new.txt").write_text("new file\n", encoding="utf-8")
    hook_bdd.diff = post_response_hooks.diff_since(hook_bdd.snapshot)


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
    hook_bdd.hooks = post_response_hooks.load_hooks(_workspace(hook_bdd))


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


@given("the structured hook trigger skips the first hook and runs the second hook")
def structured_hook_trigger_skips_first_and_runs_second(hook_bdd: HookBDD) -> None:
    hook_bdd.trigger_output = (
        '{"results": [{"index": 0, "run": false}, {"index": 1, "run": true}]}'
    )


@given("the structured hook trigger returns true")
def structured_hook_trigger_returns_true(hook_bdd: HookBDD) -> None:
    hook_bdd.trigger_output = '{"results": [{"index": 0, "run": true}]}'


@given("the structured hook trigger returns false")
def structured_hook_trigger_returns_false(hook_bdd: HookBDD) -> None:
    hook_bdd.trigger_output = '{"results": [{"index": 0, "run": false}]}'


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

    async def fake_structured_sub_agent(
        prompt: str,
        model: str | None,
        *,
        response_format: dict[str, Any] | None = None,
        messages: list[dict[str, Any]] | None = None,
        instructions: str = "",
    ) -> str:
        hook_bdd.calls.append((prompt, model, response_format))
        if response_format == post_response_hooks.TRIGGER_RESPONSE_FORMAT:
            hook_bdd.seen_messages.append(messages)
            hook_bdd.seen_instructions.append(instructions)
            return hook_bdd.trigger_output
        hook_bdd.seen_messages.append(messages)
        hook_bdd.seen_instructions.append(instructions)
        return "feedback"

    monkeypatch.setattr(
        post_response_hooks, "_run_hook_sub_agent", fake_structured_sub_agent
    )
    workspace = _workspace(hook_bdd)
    snapshot = post_response_hooks.capture_snapshot(workspace)
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
            async for event in post_response_hooks.run_hook_events(
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
    assert _hook_feedback_items(hook_bdd.events) == [
        post_response_hooks.HookFeedback("Check HTML", feedback),
    ]


@then("only the trigger uses a structured response format")
def only_trigger_uses_structured_format(hook_bdd: HookBDD) -> None:
    assert [call[1] for call in hook_bdd.calls] == [None, "hook-model"]
    assert [call[2] for call in hook_bdd.calls] == [
        post_response_hooks.TRIGGER_RESPONSE_FORMAT,
        None,
    ]


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
    trigger_calls = [
        call
        for call in hook_bdd.calls
        if call[2] == post_response_hooks.TRIGGER_RESPONSE_FORMAT
    ]
    assert len(trigger_calls) == 1
    assert trigger_calls[0][1] is None


@then("review is not called")
def review_is_not_called(hook_bdd: HookBDD) -> None:
    assert len(hook_bdd.calls) == 1
    assert hook_bdd.calls[0][2] == post_response_hooks.TRIGGER_RESPONSE_FORMAT


@then("review receives the transcript context")
def review_receives_transcript_context(hook_bdd: HookBDD) -> None:
    assert _hook_statuses(hook_bdd.events) == ["running", "triggered"]
    assert "Trigger condition" in hook_bdd.calls[0][0]
    assert hook_bdd.calls[1][0].startswith("Review HTML.")
    assert "Incremental diff" in hook_bdd.calls[1][0]
    assert "html diff" in hook_bdd.calls[1][0]
    assert hook_bdd.seen_messages == [
        None,
        [{"role": "assistant", "content": "existing context"}],
    ]
    assert hook_bdd.seen_instructions == ["", "system prompt"]


@given("a fake streaming Responses client")
def fake_streaming_responses_client(
    hook_bdd: HookBDD, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        def __aiter__(self):
            return self

        async def __anext__(self):
            if hook_bdd.captured_response_call.get("yielded"):
                raise StopAsyncIteration
            hook_bdd.captured_response_call["yielded"] = True
            return SimpleNamespace(
                type="response.output_text.done", text='{"run": true}'
            )

    class FakeResponses:
        async def create(self, **kwargs):
            hook_bdd.captured_response_call.update(kwargs)
            return FakeStream()

    class FakeClient:
        responses = FakeResponses()

        async def close(self) -> None:
            hook_bdd.captured_response_call["closed"] = True

    monkeypatch.setattr(
        post_response_hooks,
        "build_config",
        lambda: SimpleNamespace(openai_model="config-model", openai_oauth=""),
    )
    monkeypatch.setattr(
        post_response_hooks, "get_openai_client", lambda _config: FakeClient()
    )


@when("the structured hook sub-agent runs")
def structured_hook_sub_agent_runs(hook_bdd: HookBDD) -> None:
    hook_bdd.diff = asyncio.run(
        post_response_hooks._run_hook_sub_agent(
            "prompt",
            "hook-model",
            response_format=post_response_hooks.TRIGGER_RESPONSE_FORMAT,
            messages=None,
            instructions="",
        )
    )


@then("it requests streaming structured Responses input")
def requests_streaming_structured_responses_input(hook_bdd: HookBDD) -> None:
    assert hook_bdd.diff == '{"run": true}'
    assert hook_bdd.captured_response_call["model"] == "hook-model"
    assert hook_bdd.captured_response_call["input"] == [
        {"type": "message", "role": "user", "content": "prompt"}
    ]
    assert hook_bdd.captured_response_call["stream"] is True
    assert hook_bdd.captured_response_call["text"] == {
        "format": post_response_hooks.TRIGGER_RESPONSE_FORMAT
    }
    assert hook_bdd.captured_response_call["closed"] is True


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
    session = sessions.get_session(
        chat_key=sessions.get_dir_chat_key(hook_bdd.tmp_path / "workspace"),
        workspace=hook_bdd.tmp_path / "workspace",
    )
    hook_bdd.session = session
    monkeypatch.setattr(
        post_response_hooks, "capture_snapshot", lambda _workspace: object()
    )
    monkeypatch.setattr(
        post_response_hooks,
        "load_hooks",
        lambda _workspace: [post_response_hooks.HookCheck("Refactor", "any", "fix")],
    )


@given(parsers.parse('the incremental diff is "{diff}"'))
def incremental_diff_is(
    hook_bdd: HookBDD, monkeypatch: pytest.MonkeyPatch, diff: str
) -> None:
    monkeypatch.setattr(post_response_hooks, "diff_since", lambda _snapshot: diff)


@given("the hook run result is skipped")
def hook_run_result_is_skipped(
    hook_bdd: HookBDD, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_hook_sub_agent(
        _prompt: str,
        _model: str | None,
        *,
        response_format: dict[str, Any] | None = None,
        messages: list[dict[str, Any]] | None = None,
        instructions: str = "",
    ) -> str:
        assert response_format == post_response_hooks.TRIGGER_RESPONSE_FORMAT
        assert messages is None
        assert instructions == ""
        return '{"results": [{"index": 0, "run": false}]}'

    monkeypatch.setattr(post_response_hooks, "_run_hook_sub_agent", fake_hook_sub_agent)


@given(parsers.parse('the hook run result has feedback "{feedback}"'))
def hook_run_result_has_feedback(
    hook_bdd: HookBDD, monkeypatch: pytest.MonkeyPatch, feedback: str
) -> None:
    hook_batches = iter([[post_response_hooks.HookCheck("Refactor", "any", "fix")], []])
    monkeypatch.setattr(
        post_response_hooks, "load_hooks", lambda _workspace: next(hook_batches)
    )

    async def fake_hook_sub_agent(
        _prompt: str,
        _model: str | None,
        *,
        response_format: dict[str, Any] | None = None,
        messages: list[dict[str, Any]] | None = None,
        instructions: str = "",
    ) -> str:
        if response_format == post_response_hooks.TRIGGER_RESPONSE_FORMAT:
            assert messages is None
            assert instructions == ""
            return '{"results": [{"index": 0, "run": true}]}'
        assert messages is not None
        assert instructions
        return feedback

    monkeypatch.setattr(post_response_hooks, "_run_hook_sub_agent", fake_hook_sub_agent)


@when("the assistant answer is streamed")
def assistant_answer_is_streamed(
    hook_bdd: HookBDD, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_streaming_reply(**_kwargs):
        hook_bdd.stream_calls += 1
        yield SimpleNamespace(type="response.completed")

    monkeypatch.setattr(sessions, "_get_streaming_reply", fake_streaming_reply)

    async def collect_events() -> list[Any]:
        return [
            event async for event in sessions.get_answer_streaming(_session(hook_bdd))
        ]

    hook_bdd.events = asyncio.run(collect_events())


@then("hook status events are running and skipped")
def hook_status_events_are_running_and_skipped(hook_bdd: HookBDD) -> None:
    assert [event.type for event in hook_bdd.events] == [
        "response.completed",
        "faltoobot.post_response_hook.status",
        "faltoobot.post_response_hook.status",
    ]
    assert hook_bdd.events[1].status == "running"
    assert hook_bdd.events[2].hook_name == "Refactor"
    assert hook_bdd.events[2].status == "skipped"


@then("the assistant is rerun after hook feedback")
def assistant_is_rerun_after_hook_feedback(hook_bdd: HookBDD) -> None:
    assert [event.type for event in hook_bdd.events] == [
        "response.completed",
        "faltoobot.post_response_hook.status",
        "faltoobot.post_response_hook.status",
        "faltoobot.post_response_hook.feedback",
        "response.completed",
    ]
    assert hook_bdd.events[1].hook_name == "Refactor"
    assert hook_bdd.events[1].status == "running"
    assert hook_bdd.events[2].status == "triggered"
    assert hook_bdd.events[3].feedback == (
        "## Post-response hook feedback\n\n### Refactor\n\nfix it"
    )
    assert hook_bdd.stream_calls == len(["initial", "follow-up"])
    assert (
        sessions.get_messages(_session(hook_bdd))["messages"][-1]["role"] == "developer"
    )
