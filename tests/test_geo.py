"""Distances and place resolution (pure)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from toomy.client import Market
from toomy.geo import haversine_km, resolve_center, within

FIX = Path(__file__).parent / "fixtures"
MARKETS = [Market.from_api(m) for m in json.loads((FIX / "markets.json").read_text(encoding="utf-8"))["markets"]]
BY_NAME = {m.name: m for m in MARKETS}


def test_haversine_known_distances():
    assert haversine_km(50.0, 8.0, 50.0, 8.0) == 0
    # Köln Dom -> Düsseldorf Hbf is about 34 km as the crow flies
    assert haversine_km(50.9413, 6.9583, 51.2200, 6.7943) == pytest.approx(34, abs=2)
    assert haversine_km(0, 0, 0, 1) == pytest.approx(111.2, abs=0.5)  # one degree of longitude at the equator


def test_market_coordinates_are_loaded():
    z = BY_NAME["Köln-Zollstock"]
    assert z.lat == pytest.approx(50.9, abs=0.1) and z.lon == pytest.approx(6.9, abs=0.1)
    assert all(m.lat is not None and m.lon is not None for m in MARKETS)


@pytest.mark.parametrize("query, expect", [
    ("50.9413,6.9583", "coordinates"),
    ("50.9413, 6.9583", "coordinates"),
    ("Köln", "Köln-Zollstock"),
    ("koeln", None),  # transliteration is not guessed
    ("Köln-Zollstock", "market Köln-Zollstock"),
    ("3464", "market Köln-Zollstock"),  # market id
    ("50969", "postal code 50969"),
    ("509", "postal code 509"),  # prefix: approximate
])
def test_resolve_center(query, expect):
    if expect is None:
        with pytest.raises(ValueError):
            resolve_center(MARKETS, query)
        return
    lat, lon, label = resolve_center(MARKETS, query)
    assert expect in label
    assert 50 < lat < 52 and 6 < lon < 8


def test_resolve_center_errors_are_explained():
    with pytest.raises(ValueError, match="lat,lon"):
        resolve_center(MARKETS, "Atlantis")
    with pytest.raises(ValueError, match="not a valid"):
        resolve_center(MARKETS, "95.0,6.0")
    with pytest.raises(ValueError, match="empty"):
        resolve_center(MARKETS, "  ")
    with pytest.raises(ValueError, match="postal code"):
        resolve_center(MARKETS, "00000")


def test_within_is_sorted_nearest_first_and_respects_the_radius():
    lat, lon, _ = resolve_center(MARKETS, "Köln")
    near = within(MARKETS, lat, lon, 50)
    names = [m.name for _, m in near]
    assert names[0] == "Köln-Zollstock" and names[1] == "Troisdorf"
    dists = [d for d, _ in near]
    assert dists == sorted(dists) and max(dists) <= 50
    assert "Hagen" not in names and "Hagen" in [m.name for _, m in within(MARKETS, lat, lon, 80)]
    assert within(MARKETS, lat, lon, 0.0001)[0][1].name == "Köln-Zollstock"


def test_within_skips_markets_without_coordinates():
    broken = [Market(id=1, name="NoGeo", city="X", zip="1", street="", phone="", link="")]
    assert within(broken, 50.0, 7.0, 1000) == []
