"""Distances and "where is X" for toom markets. Pure functions, no I/O.

Distances are straight-line (great circle), not driving distances.
"""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from toomy.client import Market

EARTH_RADIUS_KM = 6371.0088
_LATLON = re.compile(r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*[,;]\s*(-?\d{1,3}(?:\.\d+)?)\s*$")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def _fold(s: str) -> str:
    return s.casefold().replace("ß", "ss").replace("ä", "a").replace("ö", "o").replace("ü", "u")


def _centroid(markets: list[Market]) -> tuple[float, float]:
    return (sum(m.lat for m in markets) / len(markets), sum(m.lon for m in markets) / len(markets))  # type: ignore[misc]


def resolve_center(markets: list[Market], query: str) -> tuple[float, float, str]:
    """Where "near" is. Accepts "lat,lon", a market id or exact market name, a postal code (or its first
    digits) or words in a market's name/city. No external geocoder is used, so a place without a toom market
    must be given as a postal code of a nearby market or as coordinates.

    Returns (lat, lon, human-readable description of how it was understood). Raises ValueError.
    """
    q = query.strip()
    if not q:
        raise ValueError("near must not be empty")
    located = [m for m in markets if m.lat is not None and m.lon is not None]

    m = _LATLON.match(q)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError(f"'{query}' is not a valid latitude,longitude")
        return lat, lon, f"coordinates {lat:.4f}, {lon:.4f}"

    if q.isdigit():
        by_id = [m for m in located if str(m.id) == q]
        if by_id:
            return by_id[0].lat, by_id[0].lon, f"market {by_id[0].name}"  # type: ignore[return-value]
        for n in (5, 4, 3, 2):  # postal code: the most exact prefix that still matches a market
            if len(q) < n:
                continue
            hits = [m for m in located if m.zip.startswith(q[:n])]
            if hits:
                lat, lon = _centroid(hits)
                exact = "" if n == len(q) and n == 5 else f" (postal code area {q[:n]}…, approximate)"
                return lat, lon, f"centre of {len(hits)} market(s) with postal code {q[:n]}{exact}"
        raise ValueError(f"No toom market has a postal code starting with {q[:2]}; give coordinates as 'lat,lon'.")

    exact = [m for m in located if _fold(m.name) == _fold(q)]
    if exact:
        return exact[0].lat, exact[0].lon, f"market {exact[0].name}"  # type: ignore[return-value]
    words = _fold(q).split()
    hits = [m for m in located if all(w in _fold(f"{m.name} {m.city}") for w in words)]
    if hits:
        lat, lon = _centroid(hits)
        names = ", ".join(m.name for m in hits[:3]) + ("…" if len(hits) > 3 else "")
        return lat, lon, f"centre of {len(hits)} market(s) matching '{query}' ({names})"
    raise ValueError(
        f"Could not place '{query}': no toom market matches it. Use a market name, a postal code, or coordinates 'lat,lon'."
    )


def within(markets: list[Market], lat: float, lon: float, radius_km: float) -> list[tuple[float, Market]]:
    """Markets within radius_km of the point, nearest first, as (distance_km, market)."""
    out = [
        (haversine_km(lat, lon, m.lat, m.lon), m)
        for m in markets
        if m.lat is not None and m.lon is not None
    ]
    return sorted(((d, m) for d, m in out if d <= radius_km), key=lambda t: (t[0], t[1].name))
