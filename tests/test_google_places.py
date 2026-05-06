import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from google.maps import places_v1

from faltoobot import tools
from faltoobot.config import default_config, render_config

RADIUS_METERS = 1500


def test_google_places_search_uses_api_key_field_mask_and_location_bias(
    tmp_path: Path, monkeypatch
) -> None:
    config_file = tmp_path / "config.toml"
    data = default_config()
    data["google"]["places_api_key"] = "maps-key"
    config_file.write_text(render_config(data), encoding="utf-8")
    monkeypatch.setattr(
        tools,
        "build_config",
        lambda: SimpleNamespace(
            config_file=config_file, google_places_api_key="maps-key"
        ),
    )

    calls: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, client_options: dict[str, str]) -> None:
            calls["client_options"] = client_options

        def search_text(self, request: Any, metadata: Any) -> Any:
            calls["request"] = request
            calls["metadata"] = metadata
            return places_v1.SearchTextResponse(
                places=[
                    places_v1.Place(
                        name="places/abc",
                        id="abc",
                        formatted_address="Lucknow",
                        rating=4.5,
                        display_name={"text": "Test Cafe", "language_code": "en"},
                    )
                ]
            )

    monkeypatch.setattr(tools.places_v1, "PlacesClient", FakeClient)

    result = json.loads(
        tools.google_places_search("cafes", "26.8467", "80.9462", RADIUS_METERS)
    )

    assert calls["client_options"] == {"api_key": "maps-key"}
    assert calls["metadata"] == (("x-goog-fieldmask", tools.PLACE_SEARCH_FIELD_MASK),)
    assert calls["request"].text_query == "cafes"
    assert calls["request"].location_bias.circle.radius == RADIUS_METERS
    assert result["places"][0]["display_name"]["text"] == "Test Cafe"


def test_google_place_details_gets_named_place(tmp_path: Path, monkeypatch) -> None:
    config_file = tmp_path / "config.toml"
    data = default_config()
    data["google"]["places_api_key"] = "maps-key"
    config_file.write_text(render_config(data), encoding="utf-8")
    monkeypatch.setattr(
        tools,
        "build_config",
        lambda: SimpleNamespace(
            config_file=config_file, google_places_api_key="maps-key"
        ),
    )

    calls: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, client_options: dict[str, str]) -> None:
            calls["client_options"] = client_options

        def get_place(self, request: Any, metadata: Any) -> Any:
            calls["name"] = request.name
            calls["metadata"] = metadata
            return places_v1.Place(name=request.name, formatted_address="Hazratganj")

    monkeypatch.setattr(tools.places_v1, "PlacesClient", FakeClient)

    result = json.loads(tools.google_place_details("places/abc"))

    assert calls["name"] == "places/abc"
    assert calls["metadata"] == (("x-goog-fieldmask", tools.PLACE_DETAILS_FIELD_MASK),)
    assert result["formatted_address"] == "Hazratganj"
