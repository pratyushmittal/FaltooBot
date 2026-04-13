from dataclasses import replace
from pathlib import Path
import tomllib
from typing import Any, TypeAlias, cast

from textual.binding import Binding

from faltoobot.config import app_root, load_toml
from faltoobot.faltoochat.review import ReviewView
from faltoobot.faltoochat.widgets.review_diff import ReviewDiffView

KeyList: TypeAlias = list[str]
BindingsByContext: TypeAlias = dict[str, list[Binding]]
BindingOverrides: TypeAlias = dict[str, KeyList]

REVIEW_VIEW_ACTIONS = {"review_search_project", "review_refresh_files"}


def _default_keybindings() -> BindingsByContext:
    from faltoobot.faltoochat.app import FaltooChatApp

    return {
        "app": [replace(binding) for binding in FaltooChatApp.DEFAULT_BINDINGS],
        "chat": [],
        "review": [
            *[replace(binding) for binding in ReviewView.DEFAULT_BINDINGS],
            *[replace(binding) for binding in ReviewDiffView.DEFAULT_BINDINGS],
        ],
        "modal": [],
    }


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
    default_bindings = _default_keybindings()
    known_contexts = tuple(default_bindings)
    known_actions_by_context = {
        context: {binding.action for binding in bindings}
        for context, bindings in default_bindings.items()
    }
    for context_name, context_value in data.items():
        if context_name not in known_contexts:
            errors.append(f"Unknown bindings context: {context_name}")
            continue
        if not isinstance(context_value, dict):
            errors.append(f"Bindings context must be a table: {context_name}")
            continue
        for action_name, action_value in context_value.items():
            if action_name not in known_actions_by_context[context_name]:
                errors.append(f"Unknown {context_name} binding action: {action_name}")
                continue
            keys = _parse_keys(action_value)
            if keys is None:
                errors.append(
                    f"Binding action must be a list[str]: {context_name}.{action_name}"
                )
                continue
            effective_keys = (
                keys[:1]
                if context_name == "app" and action_name == "command_palette"
                else keys
            )
            if conflict := next(
                (key for key in effective_keys if key in known_keys), None
            ):
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
    for context, defaults in _default_keybindings().items():
        merged[context] = []
        for binding in defaults:
            default_keys = [
                key.strip() for key in binding.key.split(",") if key.strip()
            ]
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
    review_diff_keys = {
        key for binding in bindings for key in binding.key.split(",") if key
    }
    if command_palette.key in review_diff_keys:
        return bindings
    return [*bindings, replace(command_palette, description="", show=False)]


def apply_faltoochat_keybindings(bindings_by_context: BindingsByContext) -> None:
    from faltoobot.faltoochat.app import FaltooChatApp

    app_bindings = list(bindings_by_context["app"])
    command_palette = next(
        binding for binding in app_bindings if binding.action == "command_palette"
    )
    FaltooChatApp.BINDINGS = cast(Any, app_bindings)  # type: ignore[attr-defined]
    FaltooChatApp.ENABLE_COMMAND_PALETTE = True  # type: ignore[attr-defined]
    FaltooChatApp.COMMAND_PALETTE_BINDING = command_palette.key  # type: ignore[attr-defined]
    FaltooChatApp._merged_bindings = FaltooChatApp._merge_bindings()  # type: ignore[attr-defined]

    review_bindings = bindings_by_context["review"]
    ReviewView.BINDINGS = [  # type: ignore[attr-defined]
        binding for binding in review_bindings if binding.action in REVIEW_VIEW_ACTIONS
    ]
    ReviewView._merged_bindings = ReviewView._merge_bindings()  # type: ignore[attr-defined]

    review_diff_bindings = [
        binding
        for binding in review_bindings
        if binding.action not in REVIEW_VIEW_ACTIONS
    ]
    review_diff_bindings = bindings_with_compact_palette_footer(
        review_diff_bindings,
        command_palette,
    )
    ReviewDiffView.BINDINGS = review_diff_bindings  # type: ignore[attr-defined]
    ReviewDiffView._merged_bindings = ReviewDiffView._merge_bindings()  # type: ignore[attr-defined]
