import asyncio
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Static, TabbedContent, TabPane

from faltoobot import sessions
from faltoobot.config import app_root
from faltoobot.faltoochat.app import FaltooChatApp
from faltoobot.faltoochat.widgets.keybinding_modals import TextModal
from faltoobot.faltoochat.widgets.review_diff import ReviewDiffView
from faltoobot.keybindings import (
    default_keybindings,
    load_keybindings,
)


class DemoModal(TextModal):
    modal_title = "Demo"


class DemoApp(App[None]):
    def compose(self) -> ComposeResult:
        yield Static("demo")


TEXT_MODAL_DEFAULT_WIDTH = 96
TEXT_MODAL_DEFAULT_HEIGHT = 24


EXPECTED_DEFAULT_APP_BINDINGS = [
    {
        "key": "ctrl+1",
        "action": "show_chat_tab",
        "description": "Chat Tab",
        "show": False,
        "priority": True,
        "key_display": None,
    },
    {
        "key": "ctrl+2",
        "action": "show_review_tab",
        "description": "Review Tab",
        "show": False,
        "priority": True,
        "key_display": None,
    },
    {
        "key": "ctrl+r",
        "action": "toggle_review_tab",
        "description": "Toggle Review Tab",
        "show": False,
        "priority": True,
        "key_display": None,
    },
    {
        "key": "ctrl+p",
        "action": "command_palette",
        "description": "Command Palette",
        "show": False,
        "priority": True,
        "key_display": None,
    },
]

EXPECTED_DEFAULT_REVIEW_BINDINGS = {
    "ReviewView": [
        {
            "key": "@",
            "action": "review_search_project",
            "description": "Search Project",
            "show": True,
            "priority": True,
            "key_display": None,
        },
        {
            "key": "R",
            "action": "review_refresh_files",
            "description": "Refresh Files",
            "show": True,
            "priority": True,
            "key_display": None,
        },
    ],
    "ReviewDiffView": [
        {
            "key": "j,ctrl+n",
            "action": "review_cursor_down",
            "description": "",
            "show": False,
            "priority": True,
            "key_display": None,
        },
        {
            "key": "k,ctrl+p",
            "action": "review_cursor_up",
            "description": "",
            "show": False,
            "priority": True,
            "key_display": None,
        },
        {
            "key": "h",
            "action": "cursor_left",
            "description": "",
            "show": False,
            "priority": True,
            "key_display": None,
        },
        {
            "key": "l",
            "action": "cursor_right",
            "description": "",
            "show": False,
            "priority": True,
            "key_display": None,
        },
        {
            "key": "g",
            "action": "review_scroll_home",
            "description": "",
            "show": False,
            "priority": True,
            "key_display": None,
        },
        {
            "key": "G",
            "action": "review_scroll_end",
            "description": "",
            "show": False,
            "priority": True,
            "key_display": None,
        },
        {
            "key": "w",
            "action": "review_next_word",
            "description": "",
            "show": False,
            "priority": True,
            "key_display": None,
        },
        {
            "key": "b",
            "action": "review_previous_word",
            "description": "",
            "show": False,
            "priority": True,
            "key_display": None,
        },
        {
            "key": "ctrl+f",
            "action": "review_page_down",
            "description": "Page Down",
            "show": True,
            "priority": True,
            "key_display": None,
        },
        {
            "key": "ctrl+b",
            "action": "review_page_up",
            "description": "Page Up",
            "show": True,
            "priority": True,
            "key_display": None,
        },
        {
            "key": "tab",
            "action": "review_next_file_tab",
            "description": "",
            "show": True,
            "priority": True,
            "key_display": None,
        },
        {
            "key": "shift+tab",
            "action": "review_previous_file_tab",
            "description": "",
            "show": False,
            "priority": True,
            "key_display": None,
        },
        {
            "key": "r",
            "action": "review_refresh_current_file",
            "description": "Refresh File",
            "show": True,
            "priority": True,
            "key_display": None,
        },
        {
            "key": "]",
            "action": "review_next_modification",
            "description": "Next Change",
            "show": True,
            "priority": True,
            "key_display": None,
        },
        {
            "key": "[",
            "action": "review_previous_modification",
            "description": "Previous Change",
            "show": True,
            "priority": True,
            "key_display": None,
        },
        {
            "key": "V",
            "action": "review_select_line",
            "description": "Select Line",
            "show": True,
            "priority": True,
            "key_display": None,
        },
        {
            "key": "W",
            "action": "review_toggle_wrap",
            "description": "Toggle Wrap",
            "show": True,
            "priority": True,
            "key_display": None,
        },
        {
            "key": "H",
            "action": "review_toggle_line_highlights",
            "description": "Line Highlights",
            "show": True,
            "priority": True,
            "key_display": None,
        },
        {
            "key": "n",
            "action": "review_jump_next",
            "description": "Next Match",
            "show": True,
            "priority": True,
            "key_display": None,
        },
        {
            "key": "N",
            "action": "review_jump_previous",
            "description": "Previous Match",
            "show": True,
            "priority": True,
            "key_display": None,
        },
        {
            "key": "*",
            "action": "review_search_word_under_cursor",
            "description": "",
            "show": False,
            "priority": True,
            "key_display": None,
        },
        {
            "key": "slash",
            "action": "review_search",
            "description": "Search File",
            "show": True,
            "priority": True,
            "key_display": None,
        },
        {
            "key": "escape",
            "action": "review_escape",
            "description": "Exit Search",
            "show": True,
            "priority": True,
            "key_display": None,
        },
        {
            "key": "m",
            "action": "review_cycle_mode",
            "description": "Review Mode",
            "show": True,
            "priority": True,
            "key_display": None,
        },
        {
            "key": "a,c",
            "action": "review_add",
            "description": "",
            "show": True,
            "priority": True,
            "key_display": None,
        },
        {
            "key": "s",
            "action": "review_stage_lines",
            "description": "",
            "show": True,
            "priority": True,
            "key_display": None,
        },
        {
            "key": "S",
            "action": "review_stage_file",
            "description": "Stage File",
            "show": True,
            "priority": True,
            "key_display": None,
        },
        {
            "key": "shift+enter",
            "action": "review_submit_reviews",
            "description": "",
            "show": True,
            "priority": True,
            "key_display": None,
        },
    ],
}


def _snapshot(bindings: list[Binding]) -> list[dict[str, str | bool | None]]:
    return [
        {
            "key": binding.key,
            "action": binding.action,
            "description": binding.description,
            "show": binding.show,
            "priority": binding.priority,
            "key_display": binding.key_display,
        }
        for binding in bindings
    ]


def _context_snapshot(bindings: list[Binding]) -> list[dict[str, object]]:
    return [
        {
            "action": binding.action,
            "keys": binding.key.split(","),
            "description": binding.description,
        }
        for binding in bindings
    ]


def _expected_context(
    snapshot: list[dict[str, str | bool | None]],
) -> list[dict[str, object]]:
    return [
        {
            "action": str(binding["action"]),
            "keys": str(binding["key"]).split(","),
            "description": str(binding["description"]),
        }
        for binding in snapshot
    ]


def _actions(bindings: list[Binding]) -> dict[str, str]:
    return {binding.action: binding.key for binding in bindings if binding.key}


def _write_bindings(home: Path, text: str) -> None:
    root = home / ".faltoobot"
    root.mkdir(parents=True, exist_ok=True)
    (root / "bindings.toml").write_text(text, encoding="utf-8")


def _git(workspace: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _build_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, FaltooChatApp]:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _git(workspace, "init")
    _git(workspace, "config", "user.email", "tests@example.com")
    _git(workspace, "config", "user.name", "Tests")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(workspace)
    return workspace, FaltooChatApp(
        session=sessions.get_session(
            chat_key=sessions.get_dir_chat_key(workspace),
            workspace=workspace,
        )
    )


def _create_modified_files(workspace: Path) -> None:
    alpha = workspace / "alpha.py"
    beta = workspace / "beta.py"
    alpha.write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    beta.write_text('value = "beta"\n', encoding="utf-8")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-m", "initial")
    alpha.write_text("a = 1\nb = 20\nc = 3\n", encoding="utf-8")
    beta.write_text('value = "beta changed"\n', encoding="utf-8")


async def _wait_for_condition(check) -> None:
    while True:
        if check():
            return
        await asyncio.sleep(0)


async def _open_review(app: FaltooChatApp, pilot) -> TabbedContent:
    await pilot.pause(0)
    await pilot.press("ctrl+2")
    await _wait_for_condition(lambda: len(app.query("#review-tabs")) == 1)
    await pilot.pause(0)
    return app.query_one("#review-tabs", TabbedContent)


def test_default_review_bindings_snapshot() -> None:
    assert {
        "ReviewView": _snapshot(
            [
                binding
                for binding in default_keybindings("review")
                if binding.action in {"review_search_project", "review_refresh_files"}
            ]
        ),
        "ReviewDiffView": _snapshot(
            [
                binding
                for binding in default_keybindings("review")
                if binding.action
                not in {"review_search_project", "review_refresh_files"}
            ]
        ),
    } == EXPECTED_DEFAULT_REVIEW_BINDINGS


def test_load_keybindings_preserves_default_review_bindings_without_bindings_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    bindings_by_context, errors = load_keybindings()

    assert _context_snapshot(bindings_by_context["app"]) == _expected_context(
        EXPECTED_DEFAULT_APP_BINDINGS
    )
    assert _context_snapshot(bindings_by_context["review"]) == _expected_context(
        EXPECTED_DEFAULT_REVIEW_BINDINGS["ReviewView"]
        + EXPECTED_DEFAULT_REVIEW_BINDINGS["ReviewDiffView"]
    )
    assert errors == []


def test_load_keybindings_keeps_default_command_palette_when_empty_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    _write_bindings(home, "[app]\ncommand_palette = []\n")

    bindings_by_context, errors = load_keybindings()

    assert _actions(bindings_by_context["app"])["command_palette"] == "ctrl+p"
    assert errors == []


def test_load_keybindings_uses_first_command_palette_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    _write_bindings(home, '[app]\ncommand_palette = ["ctrl+k", "ctrl+shift+p"]\n')

    bindings_by_context, errors = load_keybindings()

    assert _actions(bindings_by_context["app"])["command_palette"] == "ctrl+k"
    assert errors == []


def test_load_keybindings_overrides_review_actions_and_supports_explicit_unbind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    _write_bindings(
        home,
        '[app]\ncommand_palette = ["ctrl+k"]\n\n[review]\nreview_next_modification = ["z"]\nreview_toggle_wrap = []\n',
    )

    bindings_by_context, errors = load_keybindings()
    app_actions = _actions(bindings_by_context["app"])
    actions = _actions(bindings_by_context["review"])

    assert app_actions["command_palette"] == "ctrl+k"
    assert actions["review_next_modification"] == "z"
    assert "review_toggle_wrap" not in actions
    assert errors == []


@pytest.mark.anyio
async def test_text_modal_uses_default_dimensions_when_not_overridden() -> None:
    class AppWithModal(DemoApp):
        def on_mount(self) -> None:
            self.push_screen(DemoModal("hello"))

    app = AppWithModal()

    async with app.run_test() as pilot:
        await pilot.pause(0)
        dialog = app.screen.query_one("#text-modal-dialog")
        assert cast(Any, dialog.styles.width).value == TEXT_MODAL_DEFAULT_WIDTH
        assert cast(Any, dialog.styles.height).value == TEXT_MODAL_DEFAULT_HEIGHT


@pytest.mark.anyio
async def test_app_keeps_default_command_palette_binding_without_bindings_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _workspace, app = _build_app(tmp_path, monkeypatch)

    async with app.run_test() as pilot:
        await pilot.pause(0)
        assert app.COMMAND_PALETTE_BINDING == "ctrl+p"
        await pilot.press("ctrl+p")
        await pilot.pause(0)
        assert app.screen.__class__.__name__ == "CommandPalette"


@pytest.mark.anyio
async def test_app_uses_command_palette_binding_override_from_bindings_toml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    _write_bindings(home, '[app]\ncommand_palette = ["ctrl+k"]\n')
    _workspace, app = _build_app(tmp_path, monkeypatch)

    async with app.run_test() as pilot:
        await pilot.pause(0)
        assert app.COMMAND_PALETTE_BINDING == "ctrl+k"
        await pilot.press("ctrl+p")
        await pilot.pause(0)
        assert app.screen.__class__.__name__ != "CommandPalette"

        await pilot.press("ctrl+k")
        await pilot.pause(0)
        assert app.screen.__class__.__name__ == "CommandPalette"


@pytest.mark.anyio
async def test_app_system_commands_include_keybindings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _workspace, app = _build_app(tmp_path, monkeypatch)

    async with app.run_test() as pilot:
        await pilot.pause(0)
        titles = {command.title for command in app.get_system_commands(app.screen)}
        assert "Keybindings" in titles


@pytest.mark.anyio
async def test_modal_still_closes_after_switching_tabs_underneath(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = _build_app(tmp_path, monkeypatch)
    _create_modified_files(workspace)

    async with app.run_test() as pilot:
        command = next(
            command
            for command in app.get_system_commands(app.screen)
            if command.title == "Keybindings"
        )
        command.callback()
        await pilot.pause(0)

        assert app.screen.__class__.__name__ == "KeybindingsModal"

        await pilot.press("ctrl+2")
        await pilot.pause(0)
        await pilot.press("escape")
        await pilot.pause(0)

        assert app.screen.__class__.__name__ != "KeybindingsModal"


@pytest.mark.anyio
async def test_keybindings_system_command_opens_modal_with_current_bindings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    _write_bindings(
        home,
        '[app]\ncommand_palette = ["ctrl+k"]\n\n[review]\nreview_next_modification = ["z", "x"]\n',
    )
    workspace, app = _build_app(tmp_path, monkeypatch)
    _create_modified_files(workspace)

    async with app.run_test() as pilot:
        review_tabs = await _open_review(app, pilot)
        alpha_pane = next(
            pane for pane in review_tabs.query(TabPane) if pane._title == "alpha.py"
        )
        review_tabs.active = alpha_pane.id or ""
        await pilot.pause(0)

        viewer = alpha_pane.query_one(ReviewDiffView)
        viewer.focus()
        await pilot.pause(0)

        key_display = app.get_key_display(
            app.screen.active_bindings[app.COMMAND_PALETTE_BINDING].binding
        )
        command = next(
            command
            for command in app.get_system_commands(app.screen)
            if command.title == "Keybindings"
        )
        command.callback()
        await pilot.pause(0)

        assert app.screen.__class__.__name__ == "KeybindingsModal"
        subheading = cast(
            Any, app.screen.query_one("#keybindings-subheading").render()
        ).plain
        content = cast(Any, app.screen.query_one("#keybindings-content").render()).plain
        assert "Action" not in content
        assert key_display in content
        assert "Command Palette" in content
        assert str(app_root() / "bindings.toml") in subheading
        assert any(
            (("z, x" in line) or ("x, z" in line)) and "Next Change" in line
            for line in content.splitlines()
        )
        assert "review_next_modification" not in content
        assert "Copy" not in content
        assert "Paste" not in content


def test_load_keybindings_ignores_duplicate_keys_after_first_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    _write_bindings(
        home,
        '[review]\nreview_next_modification = ["z"]\nreview_previous_modification = ["z"]\n',
    )

    bindings_by_context, errors = load_keybindings()
    actions = _actions(bindings_by_context["review"])

    assert actions["review_next_modification"] == "z"
    assert actions["review_previous_modification"] == "["
    assert errors == [
        "Cannot bind [z] to [review_previous_modification]; already bound to [review_next_modification]."
    ]


def test_load_keybindings_reports_unknown_context_and_unknown_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    _write_bindings(
        home,
        '[review]\nreview_nope = ["q"]\n\n[banana]\nreview_next_modification = ["z"]\n',
    )

    bindings_by_context, errors = load_keybindings()

    assert errors == [
        "Unknown review binding action: review_nope",
        "Unknown bindings context: banana",
    ]
    assert _actions(bindings_by_context["review"])["review_next_modification"] == "]"


@pytest.mark.anyio
async def test_review_file_focus_compacts_custom_command_palette_footer_label(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    _write_bindings(home, '[app]\ncommand_palette = ["ctrl+k"]\n')
    workspace, app = _build_app(tmp_path, monkeypatch)
    _create_modified_files(workspace)

    async with app.run_test() as pilot:
        await pilot.pause(0)
        assert app.COMMAND_PALETTE_BINDING == "ctrl+k"
        assert (
            app.screen.active_bindings[app.COMMAND_PALETTE_BINDING].binding.description
            == "Command Palette"
        )

        review_tabs = await _open_review(app, pilot)
        alpha_pane = next(
            pane for pane in review_tabs.query(TabPane) if pane._title == "alpha.py"
        )
        review_tabs.active = alpha_pane.id or ""
        await pilot.pause(0)

        viewer = alpha_pane.query_one(ReviewDiffView)
        viewer.focus()
        await pilot.pause(0)

        assert (
            app.screen.active_bindings[app.COMMAND_PALETTE_BINDING].binding.description
            == ""
        )


@pytest.mark.anyio
async def test_app_uses_review_bindings_toml_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    _write_bindings(
        home,
        '[app]\ncommand_palette = ["ctrl+k"]\n\n[review]\nreview_next_modification = ["z"]\nreview_toggle_wrap = []\n',
    )
    workspace, app = _build_app(tmp_path, monkeypatch)
    _create_modified_files(workspace)

    async with app.run_test() as pilot:
        review_tabs = await _open_review(app, pilot)
        alpha_pane = next(
            pane for pane in review_tabs.query(TabPane) if pane._title == "alpha.py"
        )
        review_tabs.active = alpha_pane.id or ""
        await pilot.pause(0)

        viewer = alpha_pane.query_one(ReviewDiffView)
        viewer.focus()
        await pilot.pause(0)

        assert viewer.cursor_location == (0, 0)
        await pilot.press("]")
        await pilot.pause(0)
        assert viewer.cursor_location == (0, 0)

        await pilot.press("z")
        await pilot.pause(0)
        assert viewer.cursor_location == (1, 0)

        initial_soft_wrap = viewer.soft_wrap
        await pilot.press("W")
        await pilot.pause(0)
        assert viewer.soft_wrap is initial_soft_wrap


@pytest.mark.anyio
async def test_app_shows_duplicate_key_errors_with_descriptions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    _write_bindings(
        home,
        '[review]\nreview_next_modification = ["z"]\nreview_previous_modification = ["z"]\n',
    )
    _workspace, app = _build_app(tmp_path, monkeypatch)

    async with app.run_test() as pilot:
        await pilot.pause(0)
        assert app.screen.__class__.__name__ == "BindingsErrorModal"
        subheading = cast(
            Any, app.screen.query_one("#bindings-error-subheading").render()
        ).plain
        message = cast(
            Any, app.screen.query_one("#bindings-error-message").render()
        ).plain
        assert str(app_root() / "bindings.toml") in subheading
        assert (
            "Cannot bind [z] to [review_previous_modification]; already bound to [review_next_modification]."
            in message
        )


@pytest.mark.anyio
async def test_app_shows_dismissible_modal_for_invalid_bindings_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    _write_bindings(
        home,
        '[review]\nreview_nope = ["q"]\n\n[banana]\nreview_next_modification = ["z"]\n',
    )
    _workspace, app = _build_app(tmp_path, monkeypatch)

    async with app.run_test() as pilot:
        await pilot.pause(0)
        assert app.screen.__class__.__name__ == "BindingsErrorModal"
        dialog = app.screen.query_one("#bindings-error-dialog")
        assert (
            cast(Any, dialog.styles.width).value,
            cast(Any, dialog.styles.height).value,
        ) == (80, 24)  # noqa: PLR2004
        subheading = cast(
            Any, app.screen.query_one("#bindings-error-subheading").render()
        ).plain
        message = cast(
            Any, app.screen.query_one("#bindings-error-message").render()
        ).plain
        assert str(app_root() / "bindings.toml") in subheading
        assert "Unknown review binding action: review_nope" in message
        assert "Unknown bindings context: banana" in message

        await pilot.press("x")
        await pilot.pause(0)
        assert app.screen.__class__.__name__ != "BindingsErrorModal"
