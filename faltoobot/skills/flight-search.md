---
description: Search Google Flights through fast-flights for one-way or round-trip flight options, prices, airlines, durations, stops, and departure/arrival times.
---

Use `run_shell_call` with `uv run python` for flight searches.

Use this when the user asks to find flights, compare flight prices, or check flight options between airports/cities. Ask for missing required details before running: origin airport/city, destination airport/city, departure date, return date for round trips, passenger count, and cabin/seat class.

## Search Flights

Use IATA airport codes when possible, e.g. `LKO`, `DEL`, `BOM`, `BLR`, `DXB`. If the user gives city names, infer the common airport code only when obvious; otherwise ask a clarification.

```bash
uv run python - <<'PY'
import json
from dataclasses import asdict

from fast_flights import FlightData, Passengers, get_flights

origin = "LKO"
destination = "DEL"
departure_date = "2026-06-15"  # YYYY-MM-DD
return_date = ""               # YYYY-MM-DD, blank for one-way
seat = "economy"               # economy, premium-economy, business, first
adults = 1
children = 0
infants_in_seat = 0
infants_on_lap = 0
max_results = 5

flight_data = [
    FlightData(
        date=departure_date,
        from_airport=origin,
        to_airport=destination,
    )
]
trip = "one-way"
if return_date:
    trip = "round-trip"
    flight_data.append(
        FlightData(
            date=return_date,
            from_airport=destination,
            to_airport=origin,
        )
    )

result = get_flights(
    flight_data=flight_data,
    trip=trip,
    seat=seat,
    passengers=Passengers(
        adults=adults,
        children=children,
        infants_in_seat=infants_in_seat,
        infants_on_lap=infants_on_lap,
    ),
    fetch_mode="fallback",
)

flights = [asdict(flight) for flight in result.flights[:max_results]]
print(json.dumps(
    {
        "trip": trip,
        "origin": origin,
        "destination": destination,
        "departure_date": departure_date,
        "return_date": return_date or None,
        "current_price": result.current_price,
        "flights": flights,
    },
    ensure_ascii=False,
    indent=2,
))
PY
```

## Important

Flight data comes from a Google Flights scraper and can change quickly. Tell the user prices/availability should be verified before booking. Summarize only the best few options with airline, departure/arrival time, duration, stops, and price.
