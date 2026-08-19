import os
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, scenarios, then, when

from faltoobot import video

pytestmark = pytest.mark.external

scenarios("features/video_generation_e2e.feature")

# Published in OpenRouter's MiniMax H3 video-generation example.
PROMPT = """a slow cinematic push-in on a glowing neon sign that reads “OpenRouter” in the window of a cozy coffee shop on a rainy night, rain streaking down the glass, reflections rippling on wet pavement"""


@pytest.fixture
def video_context(tmp_path: Path) -> dict[str, Any]:
    return {"output": tmp_path / "h3.mp4"}


@given("OpenRouter video generation is enabled")
def openrouter_is_enabled(video_context: dict[str, Any]) -> None:
    if os.environ.get("RUN_VIDEO_GENERATION_E2E") != "1":
        pytest.skip("Set RUN_VIDEO_GENERATION_E2E=1 to generate a paid H3 video.")
    video_context["api_key"] = os.environ.get("OPENROUTER_API_KEY", "")
    if not video_context["api_key"]:
        pytest.skip("OPENROUTER_API_KEY is not configured.")


@when("I generate a five second H3 video")
def generate_h3_video(video_context: dict[str, Any]) -> None:
    video_context["job"] = video.generate_video(
        PROMPT,
        video_context["output"],
        api_key=video_context["api_key"],
        duration=5,
        poll_interval=10,
    )


@then("OpenRouter returns a playable MP4")
def playable_mp4(video_context: dict[str, Any]) -> None:
    assert video_context["job"]["status"] == "completed"
    data = video_context["output"].read_bytes()
    assert data
    assert b"ftyp" in data[:64]
