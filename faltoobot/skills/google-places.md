---
description: Use Google Places API for real-world places, businesses, landmarks, Maps links, opening hours, phone numbers, ratings, and local recommendations.
---

Use `run_shell_call` with `uv run --with google-maps-places python` for Google Places jobs.

Google Places needs `GOOGLE_MAPS_API_KEY` for accurate live Maps data. If it is missing, tell the user that Places API is not configured and ask whether they want to set it up; otherwise provide web search/general knowledge results.

## Search Places

Use this for local recommendations and place search, such as cafes, hospitals, restaurants, landmarks, or shops. If the user asks for "near me" and no coordinates are available, ask them to share their current WhatsApp location first.

```bash
uv run --with google-maps-places python - <<'PY'
import json
import os

from google.maps import places_v1
from proto import Message

api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
if not api_key:
    print(json.dumps({
        "places_api_available": False,
        "message": "Places API is not configured.",
        "next": "Ask if the user wants to set up GOOGLE_MAPS_API_KEY; otherwise use web search/general knowledge results.",
    }))
    raise SystemExit

query = "veg restaurants in Lucknow"
latitude = ""      # e.g. "26.8467"; blank if unknown
longitude = ""     # e.g. "80.9462"; blank if unknown
radius_meters = 0   # e.g. 3000; use 0 if coordinates are unknown
max_results = 5
field_mask = ",".join([
    "places.name",
    "places.displayName",
    "places.formattedAddress",
    "places.rating",
    "places.userRatingCount",
    "places.currentOpeningHours.openNow",
    "places.googleMapsUri",
    "places.websiteUri",
    "places.nationalPhoneNumber",
])

request = {"text_query": query, "max_result_count": max_results}
if latitude.strip() and longitude.strip() and radius_meters > 0:
    request["location_bias"] = {
        "circle": {
            "center": {"latitude": float(latitude), "longitude": float(longitude)},
            "radius": float(radius_meters),
        }
    }

client = places_v1.PlacesClient(client_options={"api_key": api_key})
response = client.search_text(
    request=places_v1.SearchTextRequest(request),
    metadata=(("x-goog-fieldmask", field_mask),),
)
places = [Message.to_dict(place, preserving_proto_field_name=True) for place in response.places]
print(json.dumps({"places": places}, ensure_ascii=False, indent=2))
PY
```

## Place Details

Use this when you already have a search result `name`, such as `places/ChIJ...`, and need exact details for that place.

```bash
uv run --with google-maps-places python - <<'PY'
import json
import os

from google.maps import places_v1
from proto import Message

api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
if not api_key:
    print(json.dumps({
        "places_api_available": False,
        "message": "Places API is not configured.",
        "next": "Ask if the user wants to set up GOOGLE_MAPS_API_KEY; otherwise use web search/general knowledge results.",
    }))
    raise SystemExit

place_name = "places/CHANGE_ME"
field_mask = ",".join([
    "name",
    "displayName",
    "formattedAddress",
    "rating",
    "userRatingCount",
    "currentOpeningHours.openNow",
    "googleMapsUri",
    "websiteUri",
    "nationalPhoneNumber",
])

client = places_v1.PlacesClient(client_options={"api_key": api_key})
response = client.get_place(
    request=places_v1.GetPlaceRequest(name=place_name),
    metadata=(("x-goog-fieldmask", field_mask),),
)
print(json.dumps(Message.to_dict(response, preserving_proto_field_name=True), ensure_ascii=False, indent=2))
PY
```

## Important

Answer concisely with name, address/area, rating count, open-now status, phone/website when useful, and Google Maps URL. Ask for current WhatsApp location only when the user asks for nearby results and no coordinates or area are available.
