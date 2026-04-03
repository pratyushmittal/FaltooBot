from pathlib import Path
from typing import Any

from openai import AsyncOpenAI, omit
from pydantic import BaseModel, Field

from faltoobot.config import Config, build_config
from faltoobot.faltoochat.diff import Diff, get_diff
from faltoobot.openai_auth import get_openai_client_options

XRAY_MODEL = "gpt-5.4-nano"


class XrayReference(BaseModel):
    path: str
    label: str
    line_number: int | None = None
    summary: str = ""


class ChangeFileOverview(BaseModel):
    path: str
    about: str
    references: list[XrayReference] = Field(default_factory=list)


class ChangeOverview(BaseModel):
    summary: str
    files: list[ChangeFileOverview] = Field(default_factory=list)


class FileSymbolOverview(BaseModel):
    name: str = Field(
        description="Name of the class, function, method, or logical node."
    )
    summary: str = Field(
        description="Very short explanation of what this node does or what changed here. Less than 15 words."
    )
    line_number: int | None = Field(
        default=None,
        description="1-based source line number for this node when it is clear from the file.",
    )
    calls: list["FileSymbolOverview"] = Field(
        default_factory=list,
        description="Nested flow of function calls that best explains how the data is flowing.",
    )


class FileOverview(BaseModel):
    important_changes: list[FileSymbolOverview] = Field(
        default_factory=list,
        description="List of important changes in this file, ordered by importance",
    )


FileSymbolOverview.model_rebuild()


def _build_client(config: Config) -> AsyncOpenAI:
    api_key, base_url, default_headers = get_openai_client_options(config)
    kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    if default_headers:
        kwargs["default_headers"] = default_headers
    return AsyncOpenAI(**kwargs)


def _relative_path(workspace: Path, path: Path) -> str:
    return str(path.resolve().relative_to(workspace.resolve()))


def _numbered_text(text: str) -> str:
    lines = text.splitlines()
    return "\n".join(f"{index + 1:4} | {line}" for index, line in enumerate(lines))


def _diff_text(diff: Diff) -> str:
    return "\n".join(
        (
            f"-{line['text']}"
            if line["type"] == "-"
            else f"+{line['text']}"
            if line["type"] == "+"
            else f" {line['text']}"
        )
        for line in diff
    )


def _change_input(workspace: Path, paths: list[Path]) -> str:
    blocks: list[str] = []
    for path in paths:
        blocks.append(
            "\n".join(
                [
                    f"FILE: {_relative_path(workspace, path)}",
                    "DIFF:",
                    _diff_text(get_diff(path)) or "(no diff)",
                ]
            )
        )
    return "\n\n".join(blocks)


def _file_input(workspace: Path, path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    return "\n".join(
        [
            f"FILE: {_relative_path(workspace, path)}",
            "DIFF:",
            _diff_text(get_diff(path)) or "(no diff)",
            "CONTENT:",
            _numbered_text(text),
        ]
    )


async def _parse_structured_output(
    *,
    config: Config,
    instructions: str,
    prompt: str,
    text_format: type[BaseModel],
) -> BaseModel:
    client = _build_client(config)
    try:
        async with client.responses.stream(
            model=XRAY_MODEL,
            instructions=instructions,
            input=[
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}],
                }
            ],
            text_format=text_format,
            store=False,
            reasoning={"effort": "high"},
            service_tier="priority" if config.openai_fast else omit,
        ) as stream:
            async for _event in stream:
                pass
            response = await stream.get_final_response()
    finally:
        await client.close()
    if response.output_parsed is None:
        raise RuntimeError("OpenAI did not return a structured overview.")
    return response.output_parsed


async def get_change_overview(
    workspace: Path,
    paths: list[Path],
    config: Config | None = None,
) -> ChangeOverview:
    config = build_config() if config is None else config
    instructions = "Return a structured change overview for a git review."
    prompt = "\n\n".join(
        [
            "Give a short overview of the overall change set.",
            "For each file, explain what changed and what the file is about.",
            "References must only use file paths that appear in the input.",
            "Use line numbers only when the diff makes the location clear. Otherwise use null.",
            _change_input(workspace, paths),
        ]
    )
    result = await _parse_structured_output(
        config=config,
        instructions=instructions,
        prompt=prompt,
        text_format=ChangeOverview,
    )
    return ChangeOverview.model_validate(result)


async def get_file_overview(
    workspace: Path,
    path: Path,
    config: Config | None = None,
) -> FileOverview:
    config = build_config() if config is None else config
    instructions = "Return a structured source-file overview for code review."
    prompt = "\n\n".join(
        [
            "Return a reviewer-friendly list of the most important changes in this file.",
            "Use the DIFF to identify what changed, and use the numbered CONTENT only for names and line numbers.",
            "Group related changes together when that makes the flow easier to understand.",
            "Use nested calls when they help explain how a change flows through the file.",
            "Use summary to explain what each node does or what changed there.",
            "All line numbers must come from the numbered file content.",
            _file_input(workspace, path),
        ]
    )
    result = await _parse_structured_output(
        config=config,
        instructions=instructions,
        prompt=prompt,
        text_format=FileOverview,
    )
    overview = FileOverview.model_validate(result)
    return overview
