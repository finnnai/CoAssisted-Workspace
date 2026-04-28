"""Google Maps tools — geocoding, places, directions, distance matrix, etc.

Maps APIs use a static API key (not OAuth). The key is read from
`config.google_maps_api_key` or `GOOGLE_MAPS_API_KEY` env var. See
`GCP_SETUP.md` Section "Maps API setup" for full setup steps.

All tools degrade cleanly when the key is missing — they return a clear error
pointing the user at the setup docs rather than crashing the MCP.

Cost reference (per 1000 calls — Google offers $200/month free credit):
    - Geocoding (forward + reverse): $5
    - Places Text Search:             $32 (Places API New, with contact data)
    - Places Nearby:                  $32
    - Place Details:                  $17 — $25
    - Directions:                     $5 — $10 (with traffic)
    - Distance Matrix:                $5 per element (origin × destination pair)
    - Time Zone:                      $5
    - Address Validation:             $17
    - Static Maps:                    $2

Most personal use stays well under the free tier ceiling.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

import gservices
from errors import format_error
from logging_util import log


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _client():
    """Return a googlemaps.Client or raise a clear error."""
    return gservices.maps()


def _maps_error_response(reason: str) -> str:
    """Return a JSON string describing the Maps-not-configured error."""
    return json.dumps({
        "status": "maps_not_configured",
        "error": reason,
        "setup_steps": [
            "1. Enable the Maps APIs in your GCP project (Geocoding, Places, "
            "Directions, Distance Matrix, Time Zone, Address Validation, Static Maps).",
            "2. Set up GCP billing (required for Maps, even on free tier).",
            "3. Create an API key at console.cloud.google.com → APIs & Services → Credentials.",
            "4. Restrict the key to the 7 Maps APIs (best practice).",
            "5. Add to config.json: {\"google_maps_api_key\": \"AIzaSy...\"} OR",
            "   export GOOGLE_MAPS_API_KEY=\"AIzaSy...\" in your shell.",
            "6. Restart Cowork.",
        ],
        "doc": "See GCP_SETUP.md for the full walkthrough.",
        "verify_with": "system_check_maps_api_key",
    }, indent=2)


# --------------------------------------------------------------------------- #
# Input models
# --------------------------------------------------------------------------- #


class GeocodeInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    address: str = Field(..., description="Free-form address string.")
    region: Optional[str] = Field(
        default=None,
        description="ccTLD bias (e.g. 'us'). Helps disambiguate names that exist in multiple countries.",
    )


class ReverseGeocodeInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    latitude: float = Field(...)
    longitude: float = Field(...)
    result_type: Optional[str] = Field(
        default=None,
        description="Filter to a result type (e.g. 'street_address', 'route', 'locality').",
    )


class SearchPlacesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    query: str = Field(
        ...,
        description="Text query, e.g. 'Italian restaurants near Palo Alto', 'Surefox North America'.",
    )
    location: Optional[str] = Field(
        default=None,
        description="Optional bias center as 'lat,lng' string (e.g. '37.4419,-122.1430').",
    )
    radius_m: Optional[int] = Field(
        default=None, ge=1, le=50000,
        description="Bias radius in meters (with location). Max 50000.",
    )
    open_now: bool = Field(default=False)
    limit: int = Field(default=10, ge=1, le=20)


class SearchNearbyInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    latitude: float = Field(...)
    longitude: float = Field(...)
    radius_m: int = Field(default=2000, ge=1, le=50000)
    keyword: Optional[str] = Field(
        default=None, description="Free-text filter (e.g. 'coffee', 'gas station')."
    )
    place_type: Optional[str] = Field(
        default=None,
        description=(
            "Google place type ('restaurant', 'cafe', 'gas_station', etc.). "
            "See https://developers.google.com/maps/documentation/places/web-service/supported_types"
        ),
    )
    open_now: bool = Field(default=False)
    limit: int = Field(default=10, ge=1, le=20)


class GetPlaceDetailsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    place_id: str = Field(
        ..., description="Google Place ID (returned by maps_search_places / maps_search_nearby)."
    )


class GetDirectionsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    origin: str = Field(..., description="Address, place name, or 'lat,lng' string.")
    destination: str = Field(..., description="Address, place name, or 'lat,lng' string.")
    mode: str = Field(
        default="driving",
        description="'driving', 'walking', 'bicycling', or 'transit'.",
    )
    waypoints: Optional[list[str]] = Field(
        default=None,
        description="Optional intermediate stops, in order.",
    )
    departure_time: Optional[str] = Field(
        default=None,
        description=(
            "ISO 8601 (e.g. '2026-04-30T14:00:00-07:00') or 'now' for traffic-aware ETAs. "
            "Driving mode only."
        ),
    )
    avoid: Optional[list[str]] = Field(
        default=None,
        description="Subset of ['tolls', 'highways', 'ferries', 'indoor'].",
    )


class DistanceMatrixInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    origins: list[str] = Field(
        ..., min_length=1, description="Origin addresses or 'lat,lng' strings."
    )
    destinations: list[str] = Field(
        ..., min_length=1, description="Destination addresses or 'lat,lng' strings."
    )
    mode: str = Field(default="driving")
    departure_time: Optional[str] = Field(default=None)


class GetTimezoneInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    latitude: float = Field(...)
    longitude: float = Field(...)
    timestamp: Optional[str] = Field(
        default=None,
        description="ISO 8601 instant. Defaults to now. Affects whether DST applies.",
    )


class ValidateAddressInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    address: str = Field(..., description="Free-form address string.")
    region_code: Optional[str] = Field(
        default=None,
        description="ISO-3166-1 alpha-2 country code, e.g. 'US'. Improves accuracy.",
    )


class StaticMapInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    center: Optional[str] = Field(
        default=None,
        description="Map center: address, place name, or 'lat,lng'. Required if no markers.",
    )
    zoom: int = Field(
        default=14, ge=0, le=21,
        description="0 (world) to 21 (max detail). 14 ≈ city level.",
    )
    size: str = Field(
        default="600x400",
        description="Image size in pixels, e.g. '600x400' (max 640x640 free, 2048x2048 paid).",
    )
    markers: Optional[list[str]] = Field(
        default=None,
        description=(
            "List of marker locations. Each entry is 'lat,lng' or address. "
            "If multiple, the map auto-fits all markers."
        ),
    )
    map_type: str = Field(
        default="roadmap",
        description="'roadmap', 'satellite', 'hybrid', or 'terrain'.",
    )
    save_to_path: Optional[str] = Field(
        default=None,
        description="If set, write PNG to this absolute path and return metadata only. Otherwise return base64.",
    )


# --------------------------------------------------------------------------- #
# Tool registration
# --------------------------------------------------------------------------- #


def register(mcp) -> None:

    @mcp.tool(
        name="maps_geocode",
        annotations={
            "title": "Geocode an address into latitude/longitude",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def maps_geocode(params: GeocodeInput) -> str:
        """Convert a free-form address into lat/lng and a canonicalized address.

        Use cases: cleaning up a contact's address, converting a venue name
        into coordinates for `maps_search_nearby`, prep for directions calls.
        """
        try:
            try:
                gmaps = _client()
            except RuntimeError as e:
                return _maps_error_response(str(e))

            results = gmaps.geocode(params.address, region=params.region)
            if not results:
                return json.dumps({"status": "no_results", "address": params.address}, indent=2)
            top = results[0]
            loc = (top.get("geometry") or {}).get("location") or {}
            return json.dumps({
                "status": "ok",
                "formatted_address": top.get("formatted_address"),
                "latitude": loc.get("lat"),
                "longitude": loc.get("lng"),
                "place_id": top.get("place_id"),
                "location_type": (top.get("geometry") or {}).get("location_type"),
                "components": top.get("address_components"),
            }, indent=2)
        except Exception as e:
            log.error("maps_geocode failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="maps_reverse_geocode",
        annotations={
            "title": "Reverse-geocode lat/lng into a human address",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def maps_reverse_geocode(params: ReverseGeocodeInput) -> str:
        """Convert lat/lng coordinates back to a human-readable address.

        Useful for showing where a map pin actually is, or for taking a tracked
        device location and turning it into a street address.
        """
        try:
            try:
                gmaps = _client()
            except RuntimeError as e:
                return _maps_error_response(str(e))
            kwargs: dict = {}
            if params.result_type:
                kwargs["result_type"] = params.result_type
            results = gmaps.reverse_geocode(
                (params.latitude, params.longitude), **kwargs
            )
            if not results:
                return json.dumps({"status": "no_results"}, indent=2)
            # Prefer POI-typed results (Empire State Building > generic
            # 5th Ave). Google's default ordering sometimes returns a
            # street_address even when the lat/lng lands on a famous
            # landmark. We scan for results whose types include a
            # POI marker AND whose own geometry is within 50m of the
            # query point.
            POI_TYPES = {
                "point_of_interest", "establishment", "tourist_attraction",
                "premise", "subpremise", "natural_feature", "park",
                "airport", "transit_station",
            }
            import math as _math
            def _haversine_m(a, b):
                lat1, lng1 = a; lat2, lng2 = b
                R = 6371000.0
                p1 = _math.radians(lat1); p2 = _math.radians(lat2)
                dp = _math.radians(lat2 - lat1); dl = _math.radians(lng2 - lng1)
                h = _math.sin(dp / 2) ** 2 + _math.cos(p1) * _math.cos(p2) * _math.sin(dl / 2) ** 2
                return 2 * R * _math.asin(_math.sqrt(h))
            top = results[0]
            poi_match = None
            for r in results:
                rtypes = set(r.get("types") or [])
                if rtypes & POI_TYPES:
                    rloc = (r.get("geometry") or {}).get("location") or {}
                    rlat, rlng = rloc.get("lat"), rloc.get("lng")
                    if rlat is not None and rlng is not None:
                        dist_m = _haversine_m(
                            (params.latitude, params.longitude), (rlat, rlng),
                        )
                        if dist_m <= 50.0:
                            poi_match = r
                            break
            chosen = poi_match if poi_match else top
            return json.dumps({
                "status": "ok",
                "formatted_address": chosen.get("formatted_address"),
                "place_id": chosen.get("place_id"),
                "components": chosen.get("address_components"),
                "preferred_poi": poi_match is not None,
                "all_results": [r.get("formatted_address") for r in results[:5]],
            }, indent=2)
        except Exception as e:
            log.error("maps_reverse_geocode failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="maps_search_places",
        annotations={
            "title": "Search Google Places by text query",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def maps_search_places(params: SearchPlacesInput) -> str:
        """Text-based search across Google's places database.

        Examples:
            'Italian restaurants near Palo Alto'
            'Tesla Supercharger off I-280'
            'Surefox North America' → finds business listings
        """
        try:
            try:
                gmaps = _client()
            except RuntimeError as e:
                return _maps_error_response(str(e))

            kwargs: dict = {"query": params.query}
            if params.location:
                lat_str, lng_str = params.location.split(",")
                kwargs["location"] = (float(lat_str), float(lng_str))
            if params.radius_m:
                kwargs["radius"] = params.radius_m
            if params.open_now:
                kwargs["open_now"] = True

            resp = gmaps.places(**kwargs)
            results = resp.get("results", [])[: params.limit]
            return json.dumps({
                "status": "ok",
                "count": len(results),
                "results": [
                    {
                        "name": r.get("name"),
                        "formatted_address": r.get("formatted_address"),
                        "place_id": r.get("place_id"),
                        "rating": r.get("rating"),
                        "user_ratings_total": r.get("user_ratings_total"),
                        "open_now": (r.get("opening_hours") or {}).get("open_now"),
                        "types": r.get("types"),
                        "location": (r.get("geometry") or {}).get("location"),
                    }
                    for r in results
                ],
            }, indent=2)
        except Exception as e:
            log.error("maps_search_places failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="maps_search_nearby",
        annotations={
            "title": "Search places near coordinates with optional type filter",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def maps_search_nearby(params: SearchNearbyInput) -> str:
        """Find places near a lat/lng, optionally filtered by type or keyword.

        Common types: 'restaurant', 'cafe', 'gas_station', 'hotel', 'parking',
        'pharmacy', 'school', 'hospital'. See Google's full list at:
        https://developers.google.com/maps/documentation/places/web-service/supported_types
        """
        try:
            try:
                gmaps = _client()
            except RuntimeError as e:
                return _maps_error_response(str(e))

            kwargs: dict = {
                "location": (params.latitude, params.longitude),
                "radius": params.radius_m,
            }
            if params.keyword:
                kwargs["keyword"] = params.keyword
            if params.place_type:
                kwargs["type"] = params.place_type
            if params.open_now:
                kwargs["open_now"] = True

            resp = gmaps.places_nearby(**kwargs)
            results = resp.get("results", [])[: params.limit]
            return json.dumps({
                "status": "ok",
                "center": {"lat": params.latitude, "lng": params.longitude},
                "radius_m": params.radius_m,
                "count": len(results),
                "results": [
                    {
                        "name": r.get("name"),
                        "vicinity": r.get("vicinity"),
                        "place_id": r.get("place_id"),
                        "rating": r.get("rating"),
                        "user_ratings_total": r.get("user_ratings_total"),
                        "open_now": (r.get("opening_hours") or {}).get("open_now"),
                        "types": r.get("types"),
                        "location": (r.get("geometry") or {}).get("location"),
                    }
                    for r in results
                ],
            }, indent=2)
        except Exception as e:
            log.error("maps_search_nearby failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="maps_get_place_details",
        annotations={
            "title": "Get full details for a place by ID",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def maps_get_place_details(params: GetPlaceDetailsInput) -> str:
        """Pull rich detail for a place: hours, phone, website, reviews, photos.

        Get the `place_id` from a prior search call. Returns standardized fields
        plus a `reviews` array (up to 5 most-helpful) and `photos` array
        (references — render via Photo API or paste into a static map).
        """
        try:
            try:
                gmaps = _client()
            except RuntimeError as e:
                return _maps_error_response(str(e))

            fields = [
                "name", "formatted_address", "formatted_phone_number",
                "international_phone_number", "website", "url", "rating",
                "user_ratings_total", "opening_hours", "current_opening_hours",
                "geometry", "type", "price_level", "review", "photo",
                "editorial_summary", "place_id", "address_component",
                "business_status",
            ]
            resp = gmaps.place(place_id=params.place_id, fields=fields)
            r = resp.get("result", {})
            return json.dumps({
                "status": "ok",
                "place_id": r.get("place_id"),
                "name": r.get("name"),
                "formatted_address": r.get("formatted_address"),
                "phone": r.get("formatted_phone_number"),
                "phone_intl": r.get("international_phone_number"),
                "website": r.get("website"),
                "google_maps_url": r.get("url"),
                "rating": r.get("rating"),
                "user_ratings_total": r.get("user_ratings_total"),
                "price_level": r.get("price_level"),
                "business_status": r.get("business_status"),
                "types": r.get("types"),
                "location": (r.get("geometry") or {}).get("location"),
                "summary": (r.get("editorial_summary") or {}).get("overview"),
                "open_now": (r.get("opening_hours") or {}).get("open_now"),
                "weekday_hours": (r.get("opening_hours") or {}).get("weekday_text"),
                "reviews": [
                    {
                        "author_name": rv.get("author_name"),
                        "rating": rv.get("rating"),
                        "relative_time": rv.get("relative_time_description"),
                        "text": rv.get("text", "")[:500],
                    }
                    for rv in (r.get("reviews") or [])[:5]
                ],
                "photo_count": len(r.get("photos") or []),
            }, indent=2)
        except Exception as e:
            log.error("maps_get_place_details failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="maps_get_directions",
        annotations={
            "title": "Get directions between two points",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def maps_get_directions(params: GetDirectionsInput) -> str:
        """Driving / walking / cycling / transit directions with optional traffic.

        Origin and destination accept addresses, place names, or 'lat,lng' strings.
        For traffic-aware driving ETAs, pass `departure_time='now'` or an ISO 8601
        timestamp. Returns concise step-by-step instructions plus total time/distance.
        """
        try:
            try:
                gmaps = _client()
            except RuntimeError as e:
                return _maps_error_response(str(e))

            kwargs: dict = {
                "origin": params.origin,
                "destination": params.destination,
                "mode": params.mode,
            }
            if params.waypoints:
                kwargs["waypoints"] = params.waypoints
            if params.avoid:
                kwargs["avoid"] = "|".join(params.avoid)
            if params.departure_time:
                if params.departure_time == "now":
                    import datetime as _dt
                    kwargs["departure_time"] = _dt.datetime.now()
                else:
                    import datetime as _dt
                    dt = _dt.datetime.fromisoformat(
                        params.departure_time.replace("Z", "+00:00")
                    )
                    kwargs["departure_time"] = dt

            routes = gmaps.directions(**kwargs)
            if not routes:
                return json.dumps({
                    "status": "no_route", "origin": params.origin, "destination": params.destination,
                }, indent=2)

            top = routes[0]
            leg = (top.get("legs") or [{}])[0]
            return json.dumps({
                "status": "ok",
                "summary": top.get("summary"),
                "mode": params.mode,
                "origin_resolved": leg.get("start_address"),
                "destination_resolved": leg.get("end_address"),
                "distance_text": (leg.get("distance") or {}).get("text"),
                "distance_meters": (leg.get("distance") or {}).get("value"),
                "duration_text": (leg.get("duration") or {}).get("text"),
                "duration_seconds": (leg.get("duration") or {}).get("value"),
                "duration_in_traffic_text": (leg.get("duration_in_traffic") or {}).get("text"),
                "steps": [
                    {
                        "instruction": _strip_html(s.get("html_instructions", "")),
                        "distance": (s.get("distance") or {}).get("text"),
                        "duration": (s.get("duration") or {}).get("text"),
                    }
                    for s in leg.get("steps", [])
                ],
            }, indent=2)
        except Exception as e:
            log.error("maps_get_directions failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="maps_distance_matrix",
        annotations={
            "title": "Distance and duration between many origins and destinations",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def maps_distance_matrix(params: DistanceMatrixInput) -> str:
        """Compute a matrix of distances/durations for many points at once.

        Use case: 'Of these 5 candidate offices, which is closest to where
        Allan, Brian, and Conor live?' — pass their home addresses as origins
        and the offices as destinations, get a 3×5 matrix back.

        Cost: $5 per element (origin × destination pair). 5 origins × 5 dests = $0.125.
        """
        try:
            try:
                gmaps = _client()
            except RuntimeError as e:
                return _maps_error_response(str(e))

            kwargs: dict = {
                "origins": params.origins,
                "destinations": params.destinations,
                "mode": params.mode,
            }
            if params.departure_time:
                import datetime as _dt
                if params.departure_time == "now":
                    kwargs["departure_time"] = _dt.datetime.now()
                else:
                    kwargs["departure_time"] = _dt.datetime.fromisoformat(
                        params.departure_time.replace("Z", "+00:00")
                    )

            resp = gmaps.distance_matrix(**kwargs)
            origins_resolved = resp.get("origin_addresses", [])
            dests_resolved = resp.get("destination_addresses", [])
            rows = resp.get("rows", [])
            matrix = []
            for i, row in enumerate(rows):
                for j, el in enumerate(row.get("elements", [])):
                    if el.get("status") != "OK":
                        matrix.append({
                            "origin": origins_resolved[i] if i < len(origins_resolved) else params.origins[i],
                            "destination": dests_resolved[j] if j < len(dests_resolved) else params.destinations[j],
                            "status": el.get("status"),
                        })
                        continue
                    matrix.append({
                        "origin": origins_resolved[i] if i < len(origins_resolved) else params.origins[i],
                        "destination": dests_resolved[j] if j < len(dests_resolved) else params.destinations[j],
                        "distance_text": (el.get("distance") or {}).get("text"),
                        "distance_meters": (el.get("distance") or {}).get("value"),
                        "duration_text": (el.get("duration") or {}).get("text"),
                        "duration_seconds": (el.get("duration") or {}).get("value"),
                        "duration_in_traffic_text": (el.get("duration_in_traffic") or {}).get("text"),
                    })
            return json.dumps({
                "status": "ok",
                "origins_count": len(params.origins),
                "destinations_count": len(params.destinations),
                "elements": matrix,
            }, indent=2)
        except Exception as e:
            log.error("maps_distance_matrix failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="maps_get_timezone",
        annotations={
            "title": "Get timezone (offset, DST, name) for a coordinate",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def maps_get_timezone(params: GetTimezoneInput) -> str:
        """Return the timezone for a lat/lng at a given moment.

        Returns IANA name, raw UTC offset, DST offset, and the local time.
        """
        try:
            try:
                gmaps = _client()
            except RuntimeError as e:
                return _maps_error_response(str(e))

            import datetime as _dt
            ts = (
                _dt.datetime.fromisoformat(params.timestamp.replace("Z", "+00:00"))
                if params.timestamp else _dt.datetime.now(_dt.timezone.utc)
            )
            resp = gmaps.timezone(
                location=(params.latitude, params.longitude), timestamp=ts,
            )
            return json.dumps({
                "status": resp.get("status"),
                "timezone_id": resp.get("timeZoneId"),
                "timezone_name": resp.get("timeZoneName"),
                "raw_offset_seconds": resp.get("rawOffset"),
                "dst_offset_seconds": resp.get("dstOffset"),
                "local_time_iso": (
                    ts + _dt.timedelta(
                        seconds=(resp.get("rawOffset") or 0) + (resp.get("dstOffset") or 0)
                    )
                ).isoformat(),
            }, indent=2)
        except Exception as e:
            log.error("maps_get_timezone failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="maps_validate_address",
        annotations={
            "title": "Validate and canonicalize a free-form address",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def maps_validate_address(params: ValidateAddressInput) -> str:
        """Clean up a messy address and flag missing components.

        Returns a verified, canonical address (proper street suffix,
        ZIP+4 in the US, etc.) plus a verdict on whether it's valid for
        delivery, has missing components, or is unconfirmed.

        More accurate than `maps_geocode` for address quality checks —
        backed by Google's Address Validation API which is purpose-built
        for this use case.
        """
        try:
            try:
                gmaps = _client()
            except RuntimeError as e:
                return _maps_error_response(str(e))

            payload: dict = {"address": {"addressLines": [params.address]}}
            if params.region_code:
                payload["address"]["regionCode"] = params.region_code

            # The Address Validation API is a separate googleapis endpoint and
            # the googlemaps Python SDK doesn't wrap it. Call it directly via
            # `requests` with the same API key. If anything fails, fall back to
            # geocode so the caller still gets useful output.
            try:
                import requests
                import os
                import config as _cfg
                api_key = (
                    _cfg.get("google_maps_api_key")
                    or os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
                )
                http_resp = requests.post(
                    f"https://addressvalidation.googleapis.com/v1:validateAddress?key={api_key}",
                    json=payload,
                    timeout=10,
                )
                http_resp.raise_for_status()
                resp = http_resp.json()
            except Exception as inner:
                # Fallback: just geocode and report the canonical address.
                log.info(
                    "maps_validate_address: AV API call failed (%s) — falling back to geocode.",
                    inner,
                )
                geo = gmaps.geocode(params.address, region=params.region_code)
                if not geo:
                    return json.dumps({"status": "no_results", "address": params.address}, indent=2)
                top = geo[0]
                return json.dumps({
                    "status": "ok_via_geocode_fallback",
                    "verdict": "geocode_only",
                    "formatted_address": top.get("formatted_address"),
                    "place_id": top.get("place_id"),
                    "note": (
                        "Used geocoding fallback. Most common cause: Address Validation "
                        "API not enabled in your GCP project. Enable it at "
                        "https://console.cloud.google.com/apis/library/addressvalidation.googleapis.com"
                    ),
                }, indent=2)

            verdict = resp.get("result", {}).get("verdict", {})
            postal = resp.get("result", {}).get("address", {}).get("postalAddress", {})
            return json.dumps({
                "status": "ok",
                "verdict": {
                    "address_complete": verdict.get("addressComplete"),
                    "has_unconfirmed": verdict.get("hasUnconfirmedComponents"),
                    "has_inferred": verdict.get("hasInferredComponents"),
                    "has_replaced": verdict.get("hasReplacedComponents"),
                    "validation_granularity": verdict.get("validationGranularity"),
                    "geocode_granularity": verdict.get("geocodeGranularity"),
                },
                "canonical_address": {
                    "lines": postal.get("addressLines"),
                    "locality": postal.get("locality"),
                    "region_code": postal.get("regionCode"),
                    "postal_code": postal.get("postalCode"),
                    "administrative_area": postal.get("administrativeArea"),
                },
                "formatted_address": resp.get("result", {})
                    .get("address", {})
                    .get("formattedAddress"),
            }, indent=2)
        except Exception as e:
            log.error("maps_validate_address failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="maps_static_map",
        annotations={
            "title": "Generate a static PNG map image",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def maps_static_map(params: StaticMapInput) -> str:
        """Render a PNG map at a center point or fitting around markers.

        Useful for embedding in emails, attaching to event invites, or pasting
        into Slack/Chat. Supports markers (auto-fit if multiple), zoom, size,
        and map type (roadmap/satellite/hybrid/terrain).

        Cost: $2 per 1000. Output: base64 PNG by default, or saved to disk
        when `save_to_path` is set.
        """
        try:
            try:
                gmaps = _client()
            except RuntimeError as e:
                return _maps_error_response(str(e))

            if not params.center and not params.markers:
                return "Error: provide either `center` or `markers`."

            # The googlemaps SDK's static_map() returns a chunked iterator of bytes.
            # SDK quirk: `size` must be a (width, height) tuple of ints, NOT a "WxH"
            # string. Parse the string form here so callers can use the friendly format.
            size_tuple = _parse_size(params.size)
            kwargs: dict = {
                "size": size_tuple,
                "zoom": params.zoom,
                "maptype": params.map_type,
            }
            if params.center:
                kwargs["center"] = params.center
            if params.markers:
                kwargs["markers"] = params.markers

            chunks = gmaps.static_map(**kwargs)
            data = b"".join(chunks) if hasattr(chunks, "__iter__") else chunks

            if params.save_to_path:
                p = Path(params.save_to_path).expanduser()
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
                return json.dumps({
                    "status": "saved",
                    "path": str(p),
                    "size_bytes": len(data),
                    "mime_type": "image/png",
                }, indent=2)

            # Inline return — but cap based on max_inline_download_kb.
            import config as _config
            max_inline = int(_config.get("max_inline_download_kb", 5120)) * 1024
            if len(data) > max_inline:
                # Auto-save to default download dir.
                auto = _config.resolve_auto_download_path("static_map.png")
                auto.write_bytes(data)
                return json.dumps({
                    "status": "auto_saved",
                    "path": str(auto),
                    "size_bytes": len(data),
                    "mime_type": "image/png",
                    "note": f"Exceeded max_inline_download_kb ({max_inline // 1024} KB).",
                }, indent=2)

            return json.dumps({
                "status": "ok",
                "size_bytes": len(data),
                "mime_type": "image/png",
                "content_b64": base64.b64encode(data).decode("ascii"),
            }, indent=2)
        except Exception as e:
            log.error("maps_static_map failed: %s", e)
            return format_error(e)


# --------------------------------------------------------------------------- #
# Module-level helpers
# --------------------------------------------------------------------------- #


def _strip_html(s: str) -> str:
    """Strip Google's HTML-formatted direction instructions to plain text."""
    import re
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", "", s)
    return s.strip()


def _parse_size(s: str | tuple | list) -> tuple[int, int]:
    """Parse a friendly '600x400' string into (600, 400). Pass-through if already a tuple/list."""
    if isinstance(s, (tuple, list)) and len(s) == 2:
        return (int(s[0]), int(s[1]))
    if isinstance(s, str) and "x" in s.lower():
        w, h = s.lower().split("x", 1)
        return (int(w.strip()), int(h.strip()))
    raise ValueError(
        f"Invalid size '{s}' — use 'WxH' string like '600x400' or [width, height] list."
    )
