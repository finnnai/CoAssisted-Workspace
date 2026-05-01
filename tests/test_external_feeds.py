# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Tests for the external feeds layer (cache + adapters)."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

import external_feeds as ef


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path):
    ef._override_cache_path_for_tests(tmp_path / "cache.json")
    ef.unfreeze()
    yield
    ef.unfreeze()
    ef._override_cache_path_for_tests(
        Path(__file__).resolve().parent.parent / "external_feeds_cache.json",
    )


# --------------------------------------------------------------------------- #
# Cache primitives
# --------------------------------------------------------------------------- #


def test_cache_returns_fresh_value():
    counter = {"hits": 0}
    def fetch():
        counter["hits"] += 1
        return {"x": 42}
    out1 = ef._cached("k1", ttl_seconds=60, fetcher=fetch)
    out2 = ef._cached("k1", ttl_seconds=60, fetcher=fetch)
    assert out1 == out2
    assert counter["hits"] == 1  # second call hit cache


def test_cache_expires_after_ttl():
    counter = {"hits": 0}
    def fetch():
        counter["hits"] += 1
        return counter["hits"]
    ef._cached("k1", ttl_seconds=1, fetcher=fetch)
    # Forcibly age the entry
    cache = ef._load_cache()
    cache["k1"]["fetched_at"] = (
        _dt.datetime.now().astimezone() - _dt.timedelta(seconds=10)
    ).isoformat()
    ef._save_cache(cache)
    ef._cached("k1", ttl_seconds=1, fetcher=fetch)
    assert counter["hits"] == 2  # second call refetched


# --------------------------------------------------------------------------- #
# Per-diem adapter
# --------------------------------------------------------------------------- #


def test_per_diem_known_city():
    p = ef.get_per_diem("San Francisco", "CA", year=2026)
    assert p.lodging_usd >= 200
    assert p.meals_usd >= 60
    assert p.state == "CA"


def test_per_diem_unknown_city_returns_default_conus():
    p = ef.get_per_diem("Tinytown", "ZZ", year=2026)
    assert p.lodging_usd >= 50  # default CONUS rate
    assert p.fiscal_year == 2026


def test_per_diem_freeze_overrides():
    ef.freeze("per_diem:CA:san francisco:2026", {
        "city": "San Francisco", "state": "CA", "fiscal_year": 2026,
        "lodging_usd": 999.0, "meals_usd": 199.0, "incidentals_usd": 5.0,
        "source": "frozen",
    })
    p = ef.get_per_diem("San Francisco", "CA", year=2026)
    assert p.lodging_usd == 999.0


# --------------------------------------------------------------------------- #
# Mileage rate adapter
# --------------------------------------------------------------------------- #


def test_mileage_rate_2026_business():
    assert ef.get_mileage_rate(2026, "business") == 0.72


def test_mileage_rate_unknown_year_uses_2026():
    assert ef.get_mileage_rate(2099) == ef.get_mileage_rate(2026)


def test_mileage_rate_medical():
    assert ef.get_mileage_rate(2026, "medical") == 0.22


# --------------------------------------------------------------------------- #
# FX rate adapter
# --------------------------------------------------------------------------- #


def test_fx_same_currency_returns_1():
    assert ef.get_fx_rate("USD", "USD") == 1.0


def test_fx_eur_to_usd_known():
    assert ef.get_fx_rate("EUR", "USD") == 1.07


def test_fx_unknown_pair_returns_1_passthrough():
    assert ef.get_fx_rate("XXX", "YYY") == 1.0


def test_fx_inverse_pair_computes_reciprocal():
    # USD → EUR isn't in the table, but EUR → USD is. Should compute 1/1.07.
    rate = ef.get_fx_rate("USD", "EUR")
    assert abs(rate - (1.0 / 1.07)) < 0.001


def test_fx_freeze_overrides():
    ef.freeze("fx:EUR:USD", 2.5)
    assert ef.get_fx_rate("EUR", "USD") == 2.5
