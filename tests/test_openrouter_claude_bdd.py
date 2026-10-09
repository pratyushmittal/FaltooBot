import asyncio
import os
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from faltoobot import gpt_utils
from faltoobot.config import Config

pytestmark = [
    pytest.mark.external,
    pytest.mark.skipif(
        not os.environ.get("OPENROUTER_API_KEY", "").strip(),
        reason="OPENROUTER_API_KEY is not set.",
    ),
]

scenarios("features/openrouter_claude.feature")

# comment: Claude does not cache short prompts, so pad the instructions.
INSTRUCTIONS = "Be terse.\n" + "Reference note: faltoobot is a helpful bot.\n" * 400


def get_weather(city: str) -> str:
    """Get the current weather for a city.

    Args:
        - city: Name of the city.
    """
    return f"{city}: 31C, sunny"


@pytest.fixture
def ctx() -> dict[str, Any]:
    return {"history": [], "usages": []}


def _ask(ctx: dict[str, Any], question: str) -> None:
    ctx["history"].append({"role": "user", "content": question})

    async def run() -> None:
        async for item in gpt_utils.get_streaming_reply(
            ctx["config"],
            instructions=INSTRUCTIONS,
            input=ctx["history"],
            tools=[get_weather],
            prompt_cache_key="openrouter-e2e",
        ):
            if getattr(item, "type", "") == "response.completed":
                ctx["usages"].append(item.response.usage.to_dict())  # type: ignore

    asyncio.run(run())


@given("Config uses an OpenRouter Claude model")
def openrouter_config(ctx: dict[str, Any]) -> None:
    ctx["config"] = cast(
        Config,
        SimpleNamespace(
            openai_model="anthropic/claude-sonnet-5.5",
            openrouter_api_key=os.environ["OPENROUTER_API_KEY"],
            openai_thinking="high",
            openai_fast=False,
        ),
    )


@when("I ask Claude a hard question that needs the weather tool")
def ask_hard_question(ctx: dict[str, Any]) -> None:
    _ask(
        ctx,
        "Think carefully in private: find the smallest prime p > 1000 such that 2 "
        "is a primitive root mod p. Then call get_weather for Lucknow. "
        "Reply with the prime and the weather only.",
    )


@when("I ask which city Claude checked")
def ask_city(ctx: dict[str, Any]) -> None:
    _ask(ctx, "Which city did you check? One word.")


@then("the weather tool was called")
def weather_tool_called(ctx: dict[str, Any]) -> None:
    assert any(
        item.get("type") == "function_call_output"
        and item.get("output") == "Lucknow: 31C, sunny"
        for item in ctx["history"]
    )


@then("the reasoning in history keeps Claude's signature")
def reasoning_has_signature(ctx: dict[str, Any]) -> None:
    reasoning = [item for item in ctx["history"] if item.get("type") == "reasoning"]
    assert reasoning
    assert all(item.get("signature") for item in reasoning)


@then("the request after the tool call reads from the prompt cache")
def tool_follow_up_is_cached(ctx: dict[str, Any]) -> None:
    assert ctx["usages"][-1]["input_tokens_details"]["cached_tokens"] > 0


@then(parsers.parse('the latest assistant answer contains "{text}"'))
def latest_answer_contains(ctx: dict[str, Any], text: str) -> None:
    content = ctx["history"][-1]["content"]
    assert text in "".join(part.get("text", "") for part in content)
