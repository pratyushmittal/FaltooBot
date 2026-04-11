from dataclasses import replace
from pathlib import Path
import tomllib
from typing import Any, TypeAlias

from textual.binding import Binding

from faltoobot.config import app_root, load_toml
from faltoobot.faltoochat.review import ReviewView
from faltoobot.faltoochat.widgets.review_diff import ReviewDiffView

KeyList: TypeAlias = list[str]
BindingsByContext: TypeAlias = dict[str, list[Binding]]
BindingOverrides: TypeAlias = dict[str, KeyList]

DEFAULT_KEYBINDINGS: BindingsByContext = {
    "app": [
        Binding("ctrl+1", "show_chat_tab", "Chat Tab", priority=True, show=False),
        Binding("ctrl+2", "show_review_tab", "Review Tab", priority=True, show=False),
        Binding(
            "ctrl+r",
            "toggle_review_tab",
            "Toggle Review Tab",
            priority=True,
            show=False,
        ),
        Binding(
            "ctrl+p",
            "command_palette",
            "Command Palette",
            show=False,
            priority=True,
            tooltip="Open the command palette",
        ),
    ],
    "chat": [],
    "review": [
        Binding("@", "review_search_project", "Search Project", priority=True, show=True),
        Binding("R", "review_refresh_files", "Refresh Files", priority=True, show=True),
        Binding("j,ctrl+n", "review_cursor_down", priority=True, show=False),
        Binding("k,ctrl+p", "review_cursor_up", priority=True, show=False),
        Binding("h", "cursor_left", priority=True, show=False),
        Binding("l", "cursor_right", priority=True, show=False),
        Binding("g", "review_scroll_home", priority=True, show=False),
        Binding("G", "review_scroll_end", priority=True, show=False),
        Binding("w", "review_next_word", priority=True, show=False),
        Binding("b", "review_previous_word", priority=True, show=False),
        Binding("ctrl+f", "review_page_down", "Page Down", priority=True, show=True),
        Binding("ctrl+b", "review_page_up", "Page Up", priority=True, show=True),
        Binding("tab", "review_next_file_tab", priority=True, show=True),
        Binding("shift+tab", "review_previous_file_tab", priority=True, show=False),
        Binding(
            "r",
            "review_refresh_current_file",
            "Refresh File",
            priority=True,
            show=True,
        ),
        Binding("]", "review_next_modification", "Next Change", priority=True, show=True),
        Binding(
            "[", "review_previous_modification", "Previous Change", priority=True, show=True
        ),
        Binding("V", "review_select_line", "Select Line", priority=True, show=True),
        Binding("W", "review_toggle_wrap", "Toggle Wrap", priority=True, show=True),
        Binding(
            "H",
            "review_toggle_line_highlights",
            "Line Highlights",
            priority=True,
            show=True,
        ),
        Binding("n", "review_jump_next", "Next Match", priority=True, show=True),
        Binding(
            "N",
            "review_jump_previous",
            "Previous Match",
            priority=True,
            show=True,
        ),
        Binding("*", "review_search_word_under_cursor", priority=True, show=False),
        Binding("slash", "review_search", "Search File", priority=True, show=True),
        Binding("escape", "review_escape", "Exit Search", priority=True, show=True),
        Binding("m", "review_cycle_mode", "Review Mode", priority=True, show=True),
        Binding("a,c", "review_add", priority=True, show=True),
        Binding("s", "review_stage_lines", priority=True, show=True),
        Binding("S", "review_stage_file", "Stage File", priority=True, show=True),
        Binding("shift+enter", "review_submit_reviews", priority=True, show=True),
    ],
    "modal": [],
}

KNOWN_CONTEXTS = tuple(DEFAULT_KEYBINDINGS)
KNOWN_ACTIONS_BY_CONTEXT: dict[str, set[str]] = {
    context: {binding.action for binding in bindings}
    for context, bindings in DEFAULT_KEYBINDINGS.items()
}
REVIEW_VIEW_ACTIONS = {"review_search_project", "review_refresh_files"}


def default_keybindings(context: str) -> list[Binding]:
    return [replace(binding) for binding in DEFAULT_KEYBINDINGS[context]]


def load_keybindings(root: Path | None = None) -> tuple[BindingsByContext, list[str]]:
    path = (root or app_root()) / "bindings.toml"
    errors: list[str] = []
    try:
        data = load_toml(path)
    except tomllib.TOMLDecodeError as error:
        data = {}
        errors.append(f"Invalid bindings file: {error}")
    overrides, validation_errors = _validate_overrides(data)
    bindings = _merge_keybindings(overrides)
    errors.extend(validation_errors)
    return bindings, errors


def _validate_overrides(data: dict[str, Any]) -> tuple[BindingOverrides, list[str]]:
    overrides: BindingOverrides = {}
    errors: list[str] = []
    known_keys: dict[str, str] = {}
    for context_name, context_value in data.items():
        if context_name not in KNOWN_CONTEXTS:
            errors.append(f"Unknown bindings context: {context_name}")
            continue
        if not isinstance(context_value, dict):
            errors.append(f"Bindings context must be a table: {context_name}")
            continue
        for action_name, action_value in context_value.items():
            if action_name not in KNOWN_ACTIONS_BY_CONTEXT[context_name]:
                errors.append(f"Unknown {context_name} binding action: {action_name}")
                continue
            keys = _parse_keys(action_value)
            if keys is None:
                errors.append(
                    f"Binding action must be a list[str]: {context_name}.{action_name}"
                )
                continue
            effective_keys = keys[:1] if context_name == "app" and action_name == "command_palette" else keys
            if conflict := next((key for key in effective_keys if key in known_keys), None):
                errors.append(
                    f"Cannot bind [{conflict}] to [{action_name}]; already bound to [{known_keys[conflict]}]."
                )
                continue
            for key in effective_keys:
                known_keys[key] = action_name
            overrides[action_name] = keys
    return overrides, errors


def _parse_keys(value: Any) -> KeyList | None:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        return None
    return [item.strip() for item in value]



def _merge_keybindings(overrides: BindingOverrides) -> BindingsByContext:
    merged: BindingsByContext = {}
    for context, defaults in DEFAULT_KEYBINDINGS.items():
        merged[context] = []
        for binding in defaults:
            default_keys = [key.strip() for key in binding.key.split(",") if key.strip()]
            keys = overrides.get(binding.action, default_keys)
            if context == "app" and binding.action == "command_palette":
                merged[context].append(replace(binding, key=(keys or default_keys)[0]))
                continue
            if keys:
                merged[context].append(replace(binding, key=",".join(keys)))
    return merged


def bindings_with_compact_palette_footer(
    bindings: list[Binding],
    command_palette: Binding,
) -> list[Binding]:
    # Textual's Footer supports `compact` and `show_command_palette`, but it
    # doesn't offer a per-view way to keep the command-palette key on the right
    # while hiding its description. Add a review-local hidden binding so the
    # focused review diff keeps the older compact footer behavior.
    review_diff_keys = {key for binding in bindings for key in binding.key.split(",") if key}
    if command_palette.key in review_diff_keys:
        return bindings
    return [*bindings, replace(command_palette, description="", show=False)]


def apply_faltoochat_keybindings(bindings_by_context: BindingsByContext) -> None:
    from faltoobot.faltoochat.app import FaltooChatApp

    app_bindings = list(bindings_by_context["app"])
    command_palette = next(
        binding for binding in app_bindings if binding.action == "command_palette"
    )
    FaltooChatApp.BINDINGS = app_bindings  # type: ignore[attr-defined]
    FaltooChatApp.ENABLE_COMMAND_PALETTE = True  # type: ignore[attr-defined]
    FaltooChatApp.COMMAND_PALETTE_BINDING = command_palette.key  # type: ignore[attr-defined]
    FaltooChatApp._merged_bindings = FaltooChatApp._merge_bindings()  # type: ignore[attr-defined]

    review_bindings = bindings_by_context["review"]
    ReviewView.BINDINGS = [  # type: ignore[attr-defined]
        binding for binding in review_bindings if binding.action in REVIEW_VIEW_ACTIONS
    ]
    ReviewView._merged_bindings = ReviewView._merge_bindings()  # type: ignore[attr-defined]

    review_diff_bindings = [
        binding for binding in review_bindings if binding.action not in REVIEW_VIEW_ACTIONS
    ]
    review_diff_bindings = bindings_with_compact_palette_footer(
        review_diff_bindings,
        command_palette,
    )
    ReviewDiffView.BINDINGS = review_diff_bindings  # type: ignore[attr-defined]
    ReviewDiffView._merged_bindings = ReviewDiffView._merge_bindings()  # type: ignore[attr-defined]
