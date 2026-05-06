import json
import os
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from google.maps import places_v1
from openai.types.responses import (
    ResponseInputFile,
    ResponseInputImage,
    ResponseInputText,
)
from proto import Message

from faltoobot import images
from faltoobot.config import build_config
from faltoobot.gpt_utils import get_openai_client
from faltoobot.openai_auth import uses_chatgpt_oauth

PLACE_SEARCH_FIELD_MASK = ",".join(
    [
        "places.name",
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.location",
        "places.rating",
        "places.userRatingCount",
        "places.priceLevel",
        "places.businessStatus",
        "places.currentOpeningHours.openNow",
        "places.googleMapsUri",
        "places.websiteUri",
        "places.nationalPhoneNumber",
        "places.types",
    ]
)
PLACE_DETAILS_FIELD_MASK = PLACE_SEARCH_FIELD_MASK.replace("places.", "")


MAX_SHELL_OUTPUT = 12_000
ToolOutput = str | list[ResponseInputText | ResponseInputImage | ResponseInputFile]


def _clipped_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        value = value.decode(errors="replace")
    return (value or "")[:MAX_SHELL_OUTPUT]


def _tool_env_overrides() -> dict[str, str]:
    env: dict[str, str] = {}
    config = build_config()
    if config.openai_api_key:
        # comment: tool examples use SDKs that read API keys from the environment.
        env["OPENAI_API_KEY"] = config.openai_api_key
    if config.gemini_api_key:
        # comment: Gemini snippets expect the key in the process environment.
        env["GEMINI_API_KEY"] = config.gemini_api_key
    if google_key := getattr(config, "google_places_api_key", ""):
        env["GOOGLE_MAPS_API_KEY"] = google_key
    return env


def _tool_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(_tool_env_overrides())
    return env


def run_shell_call_in_workspace(
    workspace: str,
    command: str,
    timeout_ms: int,
) -> str:
    try:
        process = subprocess.run(
            ["/bin/bash", "-lc", command],
            capture_output=True,
            text=False,
            timeout=timeout_ms / 1000,
            cwd=workspace,
            env=_tool_env(),
        )
    except subprocess.TimeoutExpired as exc:
        result = {
            "stdout": _clipped_text(exc.stdout),
            "stderr": _clipped_text(exc.stderr),
            "exit_code": None,
            "timed_out": True,
        }
    except Exception as exc:  # comment: tool failures should be returned to the model, not crash the chat.
        result = {
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
            "exit_code": None,
            "timed_out": False,
        }
    else:
        result = {
            "stdout": _clipped_text(process.stdout),
            "stderr": _clipped_text(process.stderr),
            "exit_code": process.returncode,
            "timed_out": False,
        }
    return json.dumps(result)


def get_run_shell_call_tool(workspace: Path) -> Callable[[str, str, int], str]:
    workspace = workspace.expanduser().resolve()

    def run_shell_call(command: str, command_summary: str, timeout_ms: int) -> str:
        return run_shell_call_in_workspace(str(workspace), command, timeout_ms)

    run_shell_call.__doc__ = f"""Returns the output of a shell command. Use it to inspect files and run CLI tasks.

    Commands are run from `{workspace}` directory.

    Args:
        - command: Bash command to run.
        - command_summary: A short one-line summary of what the command is doing. Keep it brief.
        - timeout_ms: Kill the command after this timeout in milliseconds.
    """
    return run_shell_call


async def load_image_in_workspace(workspace: str, image_path: str) -> ToolOutput:
    path = Path(image_path)
    workspace_path = Path(workspace).resolve()
    if not path.is_absolute():
        path = workspace_path / path
    resolved = path.resolve()
    config = build_config()

    if uses_chatgpt_oauth(config):
        return [images.inline_image_item(workspace_path, resolved)]

    client = get_openai_client(config)
    try:
        return [await images.upload_attachment(client, workspace_path, resolved)]
    finally:
        await client.close()


def get_load_image_tool(workspace: Path) -> Callable[[str], Awaitable[ToolOutput]]:
    workspace = workspace.expanduser().resolve()

    async def load_image(image_path: str) -> ToolOutput:
        return await load_image_in_workspace(str(workspace), image_path)

    load_image.__doc__ = """Load image files such as jpg or png. Useful for seeing screenshots and creatives.

    Args:
        - image_path: relative or absolute path of the image
    """
    return load_image


def _to_plain(value: Any) -> Any:
    """Convert Google proto/client objects to compact JSON-safe Python values."""
    if isinstance(value, Message):
        value = Message.to_dict(value, preserving_proto_field_name=True)
    elif hasattr(value, "to_dict"):
        value = value.to_dict()
    if isinstance(value, dict):
        return {
            key: plain
            for key, item in value.items()
            if (plain := _to_plain(item)) not in (None, "", [], {}, 0, 0.0)
        }
    if isinstance(value, list | tuple):
        return [
            plain
            for item in value
            if (plain := _to_plain(item)) not in ({}, [], "", None)
        ]
    return value


def _location_bias(
    latitude: str, longitude: str, radius_meters: int
) -> dict[str, Any] | None:
    """Build a Places API circular location bias when coordinates are available."""
    if not latitude.strip() or not longitude.strip() or radius_meters <= 0:
        return None
    return {
        "circle": {
            "center": {"latitude": float(latitude), "longitude": float(longitude)},
            "radius": float(radius_meters),
        }
    }


def google_places_search(
    query: str, latitude: str, longitude: str, radius_meters: int
) -> str:
    """Search Google Places API (New) for real-world places, businesses, landmarks, and local recommendations. Use it for queries where Maps/Places data is more useful than general web search.

    Args:
        - query: Natural language place search, such as "best cafes", "nearest hospital", or "veg restaurants in Lucknow".
        - latitude: Decimal latitude for the user's current location bias. Pass an empty string if unknown.
        - longitude: Decimal longitude for the user's current location bias. Pass an empty string if unknown.
        - radius_meters: Radius for the location bias in meters. Pass 0 if latitude/longitude are unknown.
    """
    config = build_config()
    if not config.google_places_api_key:
        return "Google Places is not configured. Set [google].places_api_key or GOOGLE_MAPS_API_KEY."
    request: dict[str, Any] = {"text_query": query, "max_result_count": 5}
    if bias := _location_bias(latitude, longitude, radius_meters):
        request["location_bias"] = bias
    client = places_v1.PlacesClient(
        client_options={"api_key": config.google_places_api_key}
    )
    response = client.search_text(
        request=places_v1.SearchTextRequest(request),
        metadata=(("x-goog-fieldmask", PLACE_SEARCH_FIELD_MASK),),
    )
    return json.dumps(
        _to_plain({"places": list(response.places)}), ensure_ascii=False, indent=2
    )


def google_place_details(place_name: str) -> str:
    """Get details for a Google Place returned by google_places_search. Use the place resource name from search results, for example "places/ChIJ...".

    Args:
        - place_name: Google Places resource name, usually the "name" field from google_places_search.
    """
    config = build_config()
    if not config.google_places_api_key:
        return "Google Places is not configured. Set [google].places_api_key or GOOGLE_MAPS_API_KEY."
    client = places_v1.PlacesClient(
        client_options={"api_key": config.google_places_api_key}
    )
    response = client.get_place(
        request=places_v1.GetPlaceRequest(name=place_name),
        metadata=(("x-goog-fieldmask", PLACE_DETAILS_FIELD_MASK),),
    )
    return json.dumps(_to_plain(response), ensure_ascii=False, indent=2)
