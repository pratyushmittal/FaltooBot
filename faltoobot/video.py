import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

OPENROUTER_API_BASE = "https://openrouter.ai/api/v1/"
DEFAULT_VIDEO_MODEL = "minimax/hailuo-3"
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "expired"}


def _request(
    url: str,
    api_key: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float = 60,
) -> bytes:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=data, headers=headers, method="POST" if data else "GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        message = exc.read().decode(errors="replace").strip()
        raise RuntimeError(message or f"OpenRouter returned HTTP {exc.code}") from exc


def _request_json(
    url: str,
    api_key: str,
    *,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    value = json.loads(_request(url, api_key, payload=payload))
    if not isinstance(value, dict):
        raise RuntimeError("OpenRouter returned an invalid video response")
    return value


def _image_url(url: str, frame_type: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {
        "type": "image_url",
        "image_url": {"url": url},
    }
    if frame_type is not None:
        item["frame_type"] = frame_type
    return item


def generate_video(  # noqa: PLR0913
    prompt: str,
    output: Path,
    *,
    api_key: str,
    model: str = DEFAULT_VIDEO_MODEL,
    duration: int = 5,
    resolution: str = "2K",
    aspect_ratio: str = "16:9",
    generate_audio: bool = True,
    first_frame_url: str = "",
    last_frame_url: str = "",
    reference_image_urls: list[str] | None = None,
    poll_interval: float = 30,
    timeout: float = 30 * 60,
    api_base: str = OPENROUTER_API_BASE,
) -> dict[str, Any]:
    """Generate and download a video through OpenRouter's asynchronous API."""
    api_key = api_key.strip()
    if not api_key:
        raise ValueError("OpenRouter API key is required")
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("Video prompt cannot be empty")

    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "duration": duration,
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "generate_audio": generate_audio,
    }
    frame_images = [
        _image_url(url, frame_type)
        for url, frame_type in (
            (first_frame_url, "first_frame"),
            (last_frame_url, "last_frame"),
        )
        if url
    ]
    if frame_images:
        payload["frame_images"] = frame_images
    if reference_image_urls:
        payload["input_references"] = [_image_url(url) for url in reference_image_urls]

    job = _request_json(urljoin(api_base, "videos"), api_key, payload=payload)
    deadline = time.monotonic() + timeout
    while job.get("status") not in TERMINAL_STATUSES:
        if time.monotonic() >= deadline:
            raise TimeoutError("Video generation did not finish before the timeout")
        polling_url = str(job.get("polling_url") or "")
        if not polling_url:
            raise RuntimeError("OpenRouter video job did not include a polling URL")
        time.sleep(poll_interval)
        job = _request_json(urljoin(api_base, polling_url), api_key)

    if job.get("status") != "completed":
        error = str(job.get("error") or f"Video generation {job.get('status')}")
        raise RuntimeError(error)

    urls = job.get("unsigned_urls")
    raw_url = (
        str(urls[0])
        if isinstance(urls, list) and urls
        else f"videos/{job['id']}/content?index=0"
    )
    video_url = urljoin(api_base, raw_url)
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    # External signed URLs must not receive the OpenRouter API key.
    download_key = (
        api_key if urlparse(video_url).netloc == urlparse(api_base).netloc else ""
    )
    output.write_bytes(_request(video_url, download_key, timeout=10 * 60))
    return job


def add_cli_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "generate-video", help="generate a video through OpenRouter"
    )
    prompt = parser.add_mutually_exclusive_group(required=True)
    prompt.add_argument("--prompt")
    prompt.add_argument("--prompt-file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default=DEFAULT_VIDEO_MODEL)
    parser.add_argument("--duration", type=int, default=5)
    parser.add_argument("--resolution", default="2K")
    parser.add_argument("--aspect-ratio", default="16:9")
    parser.add_argument("--no-audio", action="store_true")
    parser.add_argument("--first-frame-url", default="")
    parser.add_argument("--last-frame-url", default="")
    parser.add_argument("--reference-image-url", action="append", default=[])
    parser.add_argument("--poll-interval", type=float, default=30)
    parser.add_argument("--timeout", type=float, default=30 * 60)


def run_cli(args: argparse.Namespace, api_key: str) -> None:
    prompt = (
        args.prompt
        if args.prompt is not None
        else args.prompt_file.read_text(encoding="utf-8")
    )
    generate_video(
        prompt,
        args.output,
        api_key=api_key,
        model=args.model,
        duration=args.duration,
        resolution=args.resolution,
        aspect_ratio=args.aspect_ratio,
        generate_audio=not args.no_audio,
        first_frame_url=args.first_frame_url,
        last_frame_url=args.last_frame_url,
        reference_image_urls=args.reference_image_url,
        poll_interval=args.poll_interval,
        timeout=args.timeout,
        api_base=os.environ.get("OPENROUTER_API_BASE", OPENROUTER_API_BASE),
    )
    print(f"[Generated video]({args.output})")
