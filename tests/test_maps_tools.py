"""Baseline unit tests for tools/maps.py — P0-3 spec."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from tools import maps as t_maps
from tools.maps import (
    GeocodeInput, ReverseGeocodeInput, SearchPlacesInput,
    SearchNearbyInput, GetPlaceDetailsInput, GetDirectionsInput,
    DistanceMatrixInput, GetTimezoneInput, ValidateAddressInput,
    StaticMapInput,
)


def _resolve(name):
    from server import mcp
    return mcp._tool_manager._tools[name].fn


def _run(name, params):
    return asyncio.run(_resolve(name)(params))


def _err_assert(out):
    assert isinstance(out, str)
    # maps errors may say "no key" / "billing" / etc. — broad string check
    assert len(out) > 0


# Input validation
def test_geocode_requires_address():
    with pytest.raises(ValidationError):
        GeocodeInput()
    GeocodeInput(address="1600 Amphitheatre")


def test_reverse_geocode_requires_lat_lng():
    with pytest.raises(ValidationError):
        ReverseGeocodeInput()
    with pytest.raises(ValidationError):
        ReverseGeocodeInput(latitude=37.4)
    ReverseGeocodeInput(latitude=37.4, longitude=-122.1)


def test_search_places_requires_query():
    with pytest.raises(ValidationError):
        SearchPlacesInput()
    SearchPlacesInput(query="coffee")


def test_search_places_radius_bounds():
    SearchPlacesInput(query="x", radius_m=1)
    SearchPlacesInput(query="x", radius_m=50000)
    with pytest.raises(ValidationError):
        SearchPlacesInput(query="x", radius_m=50001)


def test_search_places_limit_bounds():
    SearchPlacesInput(query="x", limit=1)
    SearchPlacesInput(query="x", limit=20)
    with pytest.raises(ValidationError):
        SearchPlacesInput(query="x", limit=21)


def test_search_nearby_requires_lat_lng():
    with pytest.raises(ValidationError):
        SearchNearbyInput()
    SearchNearbyInput(latitude=37.4, longitude=-122.1)


def test_get_place_details_requires_place_id():
    with pytest.raises(ValidationError):
        GetPlaceDetailsInput()
    GetPlaceDetailsInput(place_id="ChIJ_abc")


def test_directions_requires_origin_and_dest():
    with pytest.raises(ValidationError):
        GetDirectionsInput()
    with pytest.raises(ValidationError):
        GetDirectionsInput(origin="A")
    GetDirectionsInput(origin="A", destination="B")


def test_distance_matrix_requires_non_empty_lists():
    with pytest.raises(ValidationError):
        DistanceMatrixInput()
    with pytest.raises(ValidationError):
        DistanceMatrixInput(origins=[], destinations=["B"])
    with pytest.raises(ValidationError):
        DistanceMatrixInput(origins=["A"], destinations=[])
    DistanceMatrixInput(origins=["A"], destinations=["B"])


def test_timezone_requires_lat_lng():
    with pytest.raises(ValidationError):
        GetTimezoneInput()
    GetTimezoneInput(latitude=37.4, longitude=-122.1)


def test_validate_address_requires_address():
    with pytest.raises(ValidationError):
        ValidateAddressInput()
    ValidateAddressInput(address="1600 Amphitheatre")


def test_static_map_zoom_bounds():
    StaticMapInput(center="x,y", zoom=0)
    StaticMapInput(center="x,y", zoom=21)
    with pytest.raises(ValidationError):
        StaticMapInput(center="x,y", zoom=22)


def test_all_maps_tools_registered():
    from server import mcp
    expected = {"maps_geocode", "maps_reverse_geocode", "maps_search_places",
                "maps_search_nearby", "maps_get_place_details",
                "maps_get_directions", "maps_distance_matrix",
                "maps_get_timezone", "maps_validate_address", "maps_static_map"}
    assert expected.issubset(set(mcp._tool_manager._tools))
