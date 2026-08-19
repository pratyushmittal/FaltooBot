import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("features/video_generation.feature")

VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp42fake-video"


@pytest.fixture
def video_context(tmp_path: Path) -> dict[str, Any]:
    return {"output": tmp_path / "generated.mp4"}


@given("a fake OpenRouter video service")
def fake_openrouter_service(video_context: dict[str, Any]) -> Iterator[None]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            video_context["payload"] = json.loads(self.rfile.read(length))
            self._json(
                {
                    "id": "video-1",
                    "status": "pending",
                    "polling_url": f"{video_context['api_base']}videos/video-1",
                }
            )

        def do_GET(self) -> None:
            if self.path == "/api/v1/videos/video-1":
                self._json(
                    {
                        "id": "video-1",
                        "status": "completed",
                        "unsigned_urls": ["/api/v1/videos/video-1/content?index=0"],
                    }
                )
                return

            video_context["download_authorization"] = self.headers.get("Authorization")
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.end_headers()
            self.wfile.write(VIDEO_BYTES)

        def _json(self, value: dict[str, Any]) -> None:
            data = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_port}/"
    video_context.update(origin=origin, api_base=f"{origin}api/v1/")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield
    server.shutdown()
    thread.join()


@when("I generate a video with the Faltoobot command")
def generate_video(video_context: dict[str, Any], tmp_path: Path) -> None:
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "OPENROUTER_API_KEY": "test-key",
        "OPENROUTER_API_BASE": video_context["api_base"],
    }
    video_context["result"] = subprocess.run(
        [
            sys.executable,
            "-m",
            "faltoobot.cli.app",
            "generate-video",
            "--prompt",
            "A paper boat crosses a rain puddle.",
            "--output",
            str(video_context["output"]),
            "--duration",
            "7",
            "--aspect-ratio",
            "9:16",
            "--first-frame-url",
            "https://example.com/first.png",
            "--reference-image-url",
            "https://example.com/subject.png",
            "--poll-interval",
            "0",
        ],
        capture_output=True,
        check=True,
        env=env,
        text=True,
        timeout=10,
    )


@then("the H3 request contains the selected video options")
def selected_video_options(video_context: dict[str, Any]) -> None:
    assert video_context["payload"] == {
        "model": "minimax/hailuo-3",
        "prompt": "A paper boat crosses a rain puddle.",
        "duration": 7,
        "resolution": "2K",
        "aspect_ratio": "9:16",
        "generate_audio": True,
        "frame_images": [
            {
                "type": "image_url",
                "image_url": {"url": "https://example.com/first.png"},
                "frame_type": "first_frame",
            }
        ],
        "input_references": [
            {
                "type": "image_url",
                "image_url": {"url": "https://example.com/subject.png"},
            }
        ],
    }


@then("the generated MP4 is saved")
def generated_mp4_is_saved(video_context: dict[str, Any]) -> None:
    result = video_context["result"]
    assert isinstance(result, subprocess.CompletedProcess)
    assert result.stdout.strip() == f"[Generated video]({video_context['output']})"
    assert video_context["output"].read_bytes() == VIDEO_BYTES
    assert video_context["download_authorization"] == "Bearer test-key"
