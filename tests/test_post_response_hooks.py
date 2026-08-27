import asyncio
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

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
    triggered_names: list[str] = field(default_factory=list)
    trigger_calls: int = 0
    review_feedback: str = ""
    response_calls: list[dict[str, Any]] = field(default_factory=list)
    client_closed: bool = False
    unknown_trigger_once: bool = False
    session: sessions.Session | None = None
    events: list[Any] = field(default_factory=list)
    stream_calls: int = 0


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


def _write_hook(ctx: HookBDD, *, name: str) -> None:
    workspace = ctx.tmp_path / "workspace"
    init_repo(workspace)
    (workspace / "page.html").write_text("<main>before</main>\n", encoding="utf-8")
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "initial")
    ctx.workspace = workspace

    ctx.hooks_dir = workspace / ".faltoobot" / "hooks"
    ctx.hooks_dir.mkdir(parents=True, exist_ok=True)
    (ctx.hooks_dir / "review.yaml").write_text(
        f"- name: {name}\n  trigger: HTML changed\n  prompt: Review HTML.\n",
        encoding="utf-8",
    )


def _hook_statuses(events: list[Any]) -> list[str]:
    return [
        event.status for event in events if event.type == "faltoobot.post_response_hook"
    ]


def _hook_feedback(events: list[Any]) -> list[str]:
    return [event.text for event in events if event.status == "feedback"]


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


@given("I capture a post-response snapshot")
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


@when("the assistant switches branches")
def assistant_switches_branches(hook_bdd: HookBDD) -> None:
    workspace = _workspace(hook_bdd)
    snapshot = asyncio.run(post_response_hooks.capture_snapshot(workspace))
    git(workspace, "switch", "-c", "review")
    (workspace / "page.html").write_text(
        "<main>review branch</main>\n", encoding="utf-8"
    )
    hook_bdd.diff = asyncio.run(post_response_hooks._diff_since(snapshot))


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


@then("the incremental hook diff is empty")
def incremental_hook_diff_is_empty(hook_bdd: HookBDD) -> None:
    assert hook_bdd.diff == ""


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


@given(parsers.parse('a hook file named "{name}"'))
def hook_file_named(hook_bdd: HookBDD, name: str) -> None:
    _write_hook(hook_bdd, name=name)


@given("two hook files")
def two_hook_files(hook_bdd: HookBDD) -> None:
    _write_hook(hook_bdd, name="Check HTML")
    (_hooks_dir(hook_bdd) / "second.yaml").write_text(
        "- name: Check CSS\n  trigger: CSS changed\n  prompt: Review CSS.\n",
        encoding="utf-8",
    )


@given("the structured hook trigger selects only the second hook")
def structured_hook_trigger_selects_only_second(hook_bdd: HookBDD) -> None:
    hook_bdd.triggered_names = ["Check CSS"]


@given("the structured hook trigger selects the hook")
def structured_hook_trigger_selects_hook(hook_bdd: HookBDD) -> None:
    hook_bdd.triggered_names = ["Check HTML"]


@given("the structured hook trigger selects no hooks")
def structured_hook_trigger_selects_no_hooks(hook_bdd: HookBDD) -> None:
    hook_bdd.triggered_names = []


@given(parsers.parse('the hook review returns "{feedback}"'))
def hook_review_returns(hook_bdd: HookBDD, feedback: str) -> None:
    hook_bdd.review_feedback = feedback


@given("HTML changes exist after a post-response snapshot")
def html_changes_exist_after_snapshot(hook_bdd: HookBDD) -> None:
    workspace = _workspace(hook_bdd)
    hook_bdd.snapshot = asyncio.run(post_response_hooks.capture_snapshot(workspace))
    (workspace / "page.html").write_text("<main>html diff</main>\n", encoding="utf-8")


@when("I run the hooks")
def run_the_hook(hook_bdd: HookBDD, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        post_response_hooks, "app_root", lambda: hook_bdd.tmp_path / "home"
    )

    async def fake_hook_trigger(
        hooks: list[post_response_hooks.HookCheck],
        diff_text: str,
    ) -> post_response_hooks.HookTriggerResponse:
        hook_bdd.trigger_calls += 1
        assert "diff" in post_response_hooks._trigger_prompt(hooks, diff_text)
        return post_response_hooks.HookTriggerResponse(
            hooks_to_run=hook_bdd.triggered_names
        )

    monkeypatch.setattr(post_response_hooks, "_run_hook_trigger", fake_hook_trigger)

    async def fake_hook_review(
        _hook: post_response_hooks.HookCheck,
        _context: post_response_hooks.HookContext,
    ) -> str:
        return hook_bdd.review_feedback

    monkeypatch.setattr(post_response_hooks, "_run_hook_review", fake_hook_review)

    async def collect_events() -> list[Any]:
        return [
            event
            async for event in post_response_hooks.run_hooks(
                _workspace(hook_bdd),
                hook_bdd.snapshot,
                post_response_hooks.HookContext(
                    [{"type": "message", "role": "user", "content": "context"}],
                    "system prompt",
                    [],
                    "session",
                    hook_bdd.tmp_path,
                ),
            )
        ]

    hook_bdd.events = asyncio.run(collect_events())


@then("the hook is triggered")
def hook_is_triggered(hook_bdd: HookBDD) -> None:
    assert _hook_statuses(hook_bdd.events) == ["running", "triggered", "feedback"]


@then("the hook is skipped")
def hook_is_skipped(hook_bdd: HookBDD) -> None:
    assert _hook_statuses(hook_bdd.events) == ["running", "skipped"]


@then("the hook is triggered without feedback")
def hook_is_triggered_without_feedback(hook_bdd: HookBDD) -> None:
    assert _hook_statuses(hook_bdd.events) == ["running", "triggered"]


@then(parsers.parse('the hook feedback is "{feedback}"'))
def hook_feedback_is(hook_bdd: HookBDD, feedback: str) -> None:
    assert feedback in _hook_feedback(hook_bdd.events)[0]


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
    assert hook_bdd.trigger_calls == 1


@then("no hook feedback is returned")
def no_hook_feedback_is_returned(hook_bdd: HookBDD) -> None:
    assert _hook_feedback(hook_bdd.events) == []


@given("a fake hook review stream")
def fake_hook_review_stream(hook_bdd: HookBDD, monkeypatch: pytest.MonkeyPatch) -> None:
    config = SimpleNamespace(openai_oauth="", openai_model="main-model")
    monkeypatch.setattr(post_response_hooks, "build_config", lambda: config)

    async def fake_streaming_reply(request_config: Any, **kwargs: Any):
        hook_bdd.response_calls.append({"config": request_config, **kwargs})
        kwargs["input"].append(
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Review feedback."}],
            }
        )
        yield SimpleNamespace(
            type="response.output_text.done",
            text="Inspecting with tools.",
        )
        yield SimpleNamespace(type="function_call_output")
        yield SimpleNamespace(
            type="response.output_text.done",
            text="Review feedback.",
        )

    monkeypatch.setattr(
        post_response_hooks, "get_streaming_reply", fake_streaming_reply
    )


@when("the hook review runs")
def hook_review_runs(hook_bdd: HookBDD) -> None:
    result = asyncio.run(
        post_response_hooks._run_hook_review(
            post_response_hooks.HookCheck("Review", "any", "Review carefully."),
            post_response_hooks.HookContext(
                [{"role": "user", "content": "history"}],
                "system prompt",
                [cast(Any, object())],
                "session",
                hook_bdd.tmp_path,
            ),
        )
    )
    assert result == "Review feedback."


@then("it uses the main model and session context")
def uses_main_model_and_session_context(hook_bdd: HookBDD) -> None:
    call = hook_bdd.response_calls[0]
    assert call["config"].openai_model == "main-model"
    assert call["input"][0] == {"role": "user", "content": "history"}
    assert "Review carefully." in call["input"][1]["content"]
    assert "do not modify files" in call["input"][1]["content"]
    assert post_response_hooks.NO_MAJOR_CHANGES_MARKER in call["input"][1]["content"]
    assert call["instructions"] == "system prompt"
    assert call["tools"]
    assert call["prompt_cache_key"] == "session"


@then("the hook turn is saved separately")
def hook_turn_is_saved_separately(hook_bdd: HookBDD) -> None:
    paths = list(hook_bdd.tmp_path.glob("post-response-hook.*.json"))
    assert len(paths) == 1
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    assert payload["hook"] == "Review"
    assert [message["role"] for message in payload["messages"]] == [
        "user",
        "assistant",
    ]


@given("a fake structured Responses client")
def fake_structured_responses_client(
    hook_bdd: HookBDD, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeStream:
        def __init__(self, hook_name: str) -> None:
            self.hook_name = hook_name

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def __aiter__(self):
            yield SimpleNamespace(
                type="response.output_text.done",
                text=post_response_hooks.HookTriggerResponse(
                    hooks_to_run=[self.hook_name]
                ).model_dump_json(),
            )

    class FakeResponses:
        def stream(self, **kwargs):
            hook_bdd.response_calls.append(kwargs)
            hook_name = (
                "Unknown hook"
                if hook_bdd.unknown_trigger_once and len(hook_bdd.response_calls) == 1
                else "Check HTML"
            )
            return FakeStream(hook_name)

    class FakeClient:
        responses = FakeResponses()

        async def close(self) -> None:
            hook_bdd.client_closed = True

    monkeypatch.setattr(
        post_response_hooks,
        "build_config",
        lambda: SimpleNamespace(),
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


@then("it requests structured Responses input")
def requests_structured_responses_input(hook_bdd: HookBDD) -> None:
    assert hook_bdd.events == [
        post_response_hooks.HookTriggerResponse(hooks_to_run=["Check HTML"])
    ]
    response_call = hook_bdd.response_calls[0]
    assert response_call["model"] == post_response_hooks.HOOK_TRIGGER_MODEL
    input_items = response_call["input"]
    assert isinstance(input_items, list)
    prompt = cast(dict[str, str], input_items[0])
    assert prompt["role"] == "user"
    assert "Check HTML" in prompt["content"]
    assert "HTML changed" in prompt["content"]
    assert "diff" in prompt["content"]
    assert (
        response_call["instructions"] == post_response_hooks.HOOK_TRIGGER_INSTRUCTIONS
    )
    assert response_call["text_format"] is post_response_hooks.HookTriggerResponse
    assert hook_bdd.client_closed is True


@then("it retries with a correction for the unknown hook")
def retries_with_unknown_hook_correction(hook_bdd: HookBDD) -> None:
    _, retry_call = hook_bdd.response_calls
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


@given(parsers.parse('the hook review returns feedback "{feedback}"'))
def hook_review_returns_feedback(
    hook_bdd: HookBDD, monkeypatch: pytest.MonkeyPatch, feedback: str
) -> None:
    hook_batches = iter(
        [[post_response_hooks.HookCheck("Refactor", "any", "Review it")], []]
    )

    async def fake_load_hooks(_workspace: Path) -> list[post_response_hooks.HookCheck]:
        return next(hook_batches)

    async def fake_hook_trigger(
        _hooks: list[post_response_hooks.HookCheck],
        _diff_text: str,
    ) -> post_response_hooks.HookTriggerResponse:
        return post_response_hooks.HookTriggerResponse(hooks_to_run=["Refactor"])

    async def fake_hook_review(
        _hook: post_response_hooks.HookCheck,
        context: post_response_hooks.HookContext,
    ) -> str:
        assert context.messages == []
        assert context.instructions == "system prompt"
        assert context.tools
        assert context.prompt_cache_key
        return feedback

    monkeypatch.setattr(post_response_hooks, "_load_hooks", fake_load_hooks)
    monkeypatch.setattr(post_response_hooks, "_run_hook_trigger", fake_hook_trigger)
    monkeypatch.setattr(post_response_hooks, "_run_hook_review", fake_hook_review)


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
            event async for event in sessions.get_answer_streaming(_session(hook_bdd))
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


@then("the assistant is rerun with the hook feedback")
def assistant_is_rerun_with_hook_feedback(hook_bdd: HookBDD) -> None:
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
    assert hook_bdd.events[3].status == "feedback"
    assert hook_bdd.stream_calls == sum(
        event.type == "response.completed" for event in hook_bdd.events
    )
    developer_message = sessions.get_messages(_session(hook_bdd))["messages"][-1]
    assert developer_message["role"] == "developer"
    assert 'feedback from "Refactor" agent' in developer_message["content"][0]["text"]
    assert "fix it" in developer_message["content"][0]["text"]


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
