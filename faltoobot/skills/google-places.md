---
description: Use Google Places API for real-world places, businesses, landmarks, Maps links, opening hours, phone numbers, ratings, and local recommendations.
---

Use this for local recommendations and place facts such as cafes nearby, addresses, ratings, opening status, phone numbers, websites, and Google Maps links.

Google Places needs `GOOGLE_MAPS_API_KEY` for accurate live Maps data. If it is missing, tell the user that Places API is not configured and ask whether they want to set it up; otherwise provide web search/general knowledge results. Ask for city/area or current WhatsApp location only when the request is "near me"/nearby.

## Workflow

1. Search with `mode = "search"`.
2. For one selected result, fetch details with `mode = "details"` and its `name`, e.g. `places/ChIJ...`.
3. Answer concisely with name, area/address, rating/count, open-now status, phone/website if useful, and Google Maps URL.
4. If coordinates are known, use `latitude`, `longitude`, and `radius_meters`; otherwise include the city/area in `query`.
5. If the user asks for "near me"/nearby results and no coordinates are available, ask them to share their current WhatsApp location before searching.

## Runner

```bash
python - <<'PY'
import json
import os
import urllib.parse
import urllib.request
from typing import Any

mode = "search"  # "search" or "details"
query = "veg restaurants in Lucknow"
place_name = "places/CHANGE_ME"
latitude = ""      # e.g. "26.8467"; blank if unknown
longitude = ""     # e.g. "80.9462"; blank if unknown
radius_meters = 0   # e.g. 3000; use 0 if coordinates are unknown
max_results = 5

SEARCH_FIELDS = ",".join([
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
])
DETAIL_FIELDS = SEARCH_FIELDS.replace("places.", "")


def fetch_json(url: str, *, method: str, fields: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not api_key:
        return {
            "places_api_available": False,
            "message": "GOOGLE_MAPS_API_KEY is not configured.",
            "suggested_next_step": "Tell the user that Places API is not configured. Ask if they want to set up [google].places_api_key / GOOGLE_MAPS_API_KEY; otherwise provide web search/general knowledge results. Ask for city/area or current WhatsApp location for nearby requests.",
        }
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": fields,
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode())

if mode == "search":
    body: dict[str, Any] = {"textQuery": query, "maxResultCount": max_results}
    if latitude.strip() and longitude.strip() and radius_meters > 0:
        body["locationBias"] = {
            "circle": {
                "center": {"latitude": float(latitude), "longitude": float(longitude)},
                "radius": float(radius_meters),
            }
        }
    output = fetch_json(
        "https://places.googleapis.com/v1/places:searchText",
        method="POST",
        fields=SEARCH_FIELDS,
        body=body,
    )
elif mode == "details":
    output = fetch_json(
        "https://places.googleapis.com/v1/" + urllib.parse.quote(place_name, safe="/"),
        method="GET",
        fields=DETAIL_FIELDS,
    )
else:
    raise SystemExit(f"Unsupported mode: {mode}")

print(json.dumps(output, ensure_ascii=False, indent=2))
PY
```
