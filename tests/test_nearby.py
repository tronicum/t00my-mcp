"""get_article, markets_nearby and find_article_nearby end to end against the fake toom.de."""

from __future__ import annotations

import copy
import json
from urllib.parse import quote

import httpx
import pytest
from conftest import ARTICLE_STATE, DOOR, call, call_err

from toomy import server

KOELN, TROISDORF, BERGHEIM, LANGENFELD, GREVENBROICH = 3464, 3248, 3668, 3660, 3793
PRENZLAU, FRIEDRICHSHAIN = 3839, 3646
A, B = "1490300", "10458992"
STOCKS = "/shop/rest/V1/toom/stocks/availability"
# Köln within 50 km, nearest first (straight line)
KOELN_ORDER = [
    "Köln-Zollstock", "Troisdorf", "Bergheim-Zieverich", "Langenfeld", "Grevenbroich",
    "Düren", "Jülich", "Remagen", "Gummersbach", "Mönchengladbach",
]


def basket_writes(toom):
    return [r for r in toom.requests if "/basket" in r[1] and r[0] != "GET"]


def break_stock(toom, monkeypatch, *market_ids):
    """Stock requests of the given markets answer HTTP 503; everything else is served by the fake."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == STOCKS:
            toom.requests.append((request.method, request.url.path, None))
            for mid in market_ids:
                if f"market_id={mid}" in request.headers.get("cookie", ""):
                    return httpx.Response(503, text="Service Unavailable")
        return toom.handler(request)
    monkeypatch.setattr(server, "_transport", httpx.MockTransport(handler))


def stock_requests(toom):
    return [r for r in toom.requests if r[1] == STOCKS]


# ------------------------------------------------------------ get_article

async def test_get_article_details_of_the_door(mcp, toom):
    res = await call(mcp, "get_article", sku=DOOR, market="Prenzlau")
    art = res["article"]
    assert art["sku"] == DOOR
    assert art["title"] == "Feuerschutztür 'T30-1 H8-5' 87,5 x 200 cm"
    assert art["brand"] == "Hörmann"
    assert art["price_eur"] == 249.99
    assert art["ean"] == "4005954806827"
    assert art["url"] == "https://toom.de/p/feuerschutztuer-t30-1-h8-5-875-x-200-cm/110013"
    assert art["characteristics"]["masse-gewicht"] == {"Breite": "87,5 cm", "Höhe": "200 cm"}
    assert art["characteristics"]["weitere-eigenschaften"]["Farbe"] == ["Grau", "Weiß"]
    assert art["characteristics"]["weitere-eigenschaften"]["Material"] == "Metall"  # single value stays a string
    assert art["categories"] == ["Bauen & Renovieren", "Fenster, Türen & Vordächer", "Eingangstüren", "Stahltüren"]
    assert art["manufacturer"]["name"] == "Hörmann KG Verkaufsgesellschaft"
    assert art["manufacturer"]["email"] == "info@hoermann.de"
    assert len(art["summary"]) == 5
    assert "produktdetails" not in art["characteristics"] and "herstellerinformationen" not in art["characteristics"]
    assert res["market"] == "Prenzlau" and res["quantity_asked"] == 1


async def test_get_article_caps_images_at_six(mcp, toom, monkeypatch):
    state = copy.deepcopy(ARTICLE_STATE)
    state["content"]["gallery"] = [{"src": f"https://static.toom.de/img_{i}.png"} for i in range(9)]
    enc = quote(json.dumps(state, ensure_ascii=False, separators=(",", ":")), safe="-_.!~*'()")
    page = f'<html><body><div id="root" data-props="{enc}"></div><h1>x</h1></body></html>'

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/p/"):
            return httpx.Response(200, text=page)
        return toom.handler(request)

    monkeypatch.setattr(server, "_transport", httpx.MockTransport(handler))
    res = await call(mcp, "get_article", sku=DOOR, market="Prenzlau")
    assert res["article"]["images"] == [f"https://static.toom.de/img_{i}.png" for i in range(6)]


async def test_get_article_stock_is_capped_at_quantity(mcp, toom):
    toom.set_stock(PRENZLAU, DOOR, 3)
    res = await call(mcp, "get_article", sku=DOOR, market="Prenzlau", quantity=5)
    assert res["stock"] == {"Prenzlau": 3}
    res = await call(mcp, "get_article", sku=DOOR, market="Prenzlau", quantity=2)
    assert res["stock"] == {"Prenzlau": 2}
    toom.set_stock(PRENZLAU, DOOR, 0)
    assert (await call(mcp, "get_article", sku=DOOR, market="Prenzlau"))["stock"] == {"Prenzlau": 0}


async def test_get_article_uses_the_selected_market(mcp, toom):
    await call(mcp, "set_market", market="Friedrichshain")
    toom.set_stock(FRIEDRICHSHAIN, DOOR, 4)
    res = await call(mcp, "get_article", sku=DOOR, quantity=9)
    assert res["market"] == "Berlin-Friedrichshain" and res["stock"] == {"Berlin-Friedrichshain": 4}


async def test_get_article_also_check_adds_markets_without_duplicates(mcp, toom):
    toom.set_stock(PRENZLAU, DOOR, 1)
    toom.set_stock(KOELN, DOOR, 0)
    toom.set_stock(TROISDORF, DOOR, 7)
    res = await call(mcp, "get_article", sku=DOOR, market="Prenzlau", quantity=3,
                     also_check=["Köln-Zollstock", "3248", "Prenzlau"])
    assert res["stock"] == {"Prenzlau": 1, "Köln-Zollstock": 0, "Troisdorf": 3}
    assert list(res["stock"]) == ["Prenzlau", "Köln-Zollstock", "Troisdorf"]  # selected market first
    assert len(stock_requests(toom)) == 3  # Prenzlau was not asked twice
    assert basket_writes(toom) == []


async def test_get_article_failing_market_is_reported_unknown(mcp, toom, monkeypatch):
    break_stock(toom, monkeypatch, KOELN)
    toom.set_stock(PRENZLAU, DOOR, 2)
    res = await call(mcp, "get_article", sku=DOOR, market="Prenzlau", also_check=["Köln-Zollstock"])
    assert res["stock"]["Prenzlau"] == 1  # quantity default 1 caps the stock of 2
    assert res["stock"]["Köln-Zollstock"].startswith("unknown (")
    assert "HTTP 503" in res["stock"]["Köln-Zollstock"]
    assert res["article"]["sku"] == DOOR


async def test_get_article_unknown_number(mcp, toom):
    toom_msg = await call_err(mcp, "get_article", sku="9999999", market="Prenzlau")
    assert "No article with the number '9999999'" in toom_msg
    assert stock_requests(toom) == []


async def test_get_article_quantity_must_be_positive(mcp, toom):
    assert "quantity must be >= 1" in await call_err(mcp, "get_article", sku=DOOR, market="Prenzlau", quantity=0)
    assert "quantity must be >= 1" in await call_err(mcp, "get_article", sku=DOOR, market="Prenzlau", quantity=-2)
    assert stock_requests(toom) == []


async def test_get_article_needs_a_market(mcp, toom):
    assert "No market selected" in await call_err(mcp, "get_article", sku=DOOR)


async def test_get_article_unknown_also_check_market(mcp, toom):
    err = await call_err(mcp, "get_article", sku=DOOR, market="Prenzlau", also_check=["atlantis"])
    assert "No toom market found" in err


# ----------------------------------------------------------- markets_nearby

async def test_markets_nearby_sorted_nearest_first(mcp, toom):
    res = await call(mcp, "markets_nearby", near="Köln")
    assert res["total"] == 10
    assert [m["name"] for m in res["markets"]] == KOELN_ORDER
    dists = [m["distance_km"] for m in res["markets"]]
    assert dists == sorted(dists)
    assert dists[0] == 0.0
    assert dists[1] == pytest.approx(14.7, abs=0.2)
    assert res["markets"][0]["id"] == KOELN and res["markets"][1]["id"] == TROISDORF
    assert res["radius_km"] == 50
    assert res["centre"]["lat"] == pytest.approx(50.906, abs=0.001)
    assert res["centre"]["lon"] == pytest.approx(6.9341, abs=0.001)
    assert "Köln-Zollstock" in res["centre"]["understood_as"]


async def test_markets_nearby_radius_is_respected(mcp, toom):
    res = await call(mcp, "markets_nearby", near="Köln", radius_km=20)
    assert [m["name"] for m in res["markets"]] == ["Köln-Zollstock", "Troisdorf"]
    assert res["total"] == 2
    assert all(m["distance_km"] <= 20 for m in res["markets"])
    res = await call(mcp, "markets_nearby", near="Köln", radius_km=1)
    assert [m["name"] for m in res["markets"]] == ["Köln-Zollstock"]


async def test_markets_nearby_limit_truncates_but_total_stays(mcp, toom):
    res = await call(mcp, "markets_nearby", near="Köln", limit=3)
    assert res["total"] == 10
    assert [m["name"] for m in res["markets"]] == KOELN_ORDER[:3]


async def test_markets_nearby_coordinates(mcp, toom):
    res = await call(mcp, "markets_nearby", near="50.94,6.96", radius_km=10)
    assert [m["name"] for m in res["markets"]] == ["Köln-Zollstock"]
    assert res["centre"] == {"lat": 50.94, "lon": 6.96, "understood_as": "coordinates 50.9400, 6.9600"}
    assert 3 < res["markets"][0]["distance_km"] < 6


async def test_markets_nearby_postal_code(mcp, toom):
    res = await call(mcp, "markets_nearby", near="53842", radius_km=10)  # Troisdorf
    assert [m["name"] for m in res["markets"]] == ["Troisdorf"]
    assert res["markets"][0]["distance_km"] == 0.0
    assert "postal code" in res["centre"]["understood_as"]


async def test_markets_nearby_errors(mcp, toom):
    assert "Could not place 'Atlantis'" in await call_err(mcp, "markets_nearby", near="Atlantis")
    assert "No toom market has a postal code starting with 00" in await call_err(mcp, "markets_nearby", near="00000")
    for bad in (0, -5, 300.1, 1000):
        assert "radius_km must be between 0 and 300" in await call_err(mcp, "markets_nearby", near="Köln", radius_km=bad)
    assert (await call(mcp, "markets_nearby", near="Köln", radius_km=300))["radius_km"] == 300  # upper bound is valid


async def test_markets_nearby_empty_radius_is_not_an_error(mcp, toom):
    res = await call(mcp, "markets_nearby", near="Prenzlau", radius_km=1)
    assert [m["name"] for m in res["markets"]] == ["Prenzlau"]
    res = await call(mcp, "markets_nearby", near="0,0", radius_km=5)
    assert res["total"] == 0 and res["markets"] == []


# ------------------------------------------------------ find_article_nearby

async def test_find_article_complete_markets_first_then_nearest(mcp, toom):
    toom.set_stock(KOELN, DOOR, 0)  # nearest has none
    toom.set_stock(TROISDORF, DOOR, 1)  # second has 1 of the 2 wanted
    toom.set_stock(BERGHEIM, DOOR, 5)
    res = await call(mcp, "find_article_nearby", near="Köln", sku=DOOR, quantity=2, radius_km=25)
    names = [m["name"] for m in res["markets"]]
    assert names == ["Bergheim-Zieverich", "Langenfeld", "Köln-Zollstock", "Troisdorf"]
    flags = [m["complete"] for m in res["markets"]]
    assert flags == [True, True, False, False]
    assert res["nearest_complete"]["name"] == "Bergheim-Zieverich"
    assert res["nearest_complete"]["id"] == BERGHEIM
    assert res["nearest_complete"]["stock"] == {DOOR: 2}  # capped at the wanted quantity
    assert res["markets_in_radius"] == 4 and res["checked"] == 4 and res["not_checked"] == []
    zollstock = res["markets"][2]
    assert zollstock["stock"] == {DOOR: 0}
    assert zollstock["short"] == [{"sku": DOOR, "title": "Feuerschutztür 'T30-1 H8-5' 87,5 x 200 cm", "wanted": 2, "available": 0}]
    assert res["markets"][3]["short"][0]["available"] == 1
    assert res["markets"][0]["short"] == []
    assert res["articles"] == [{"sku": DOOR, "title": "Feuerschutztür 'T30-1 H8-5' 87,5 x 200 cm", "quantity": 2}]
    assert res["radius_km"] == 25
    assert all("distance_km" in m and "phone" in m for m in res["markets"])
    assert basket_writes(toom) == []


async def test_find_article_nearest_complete_skips_markets_without_stock(mcp, toom):
    toom.set_stock(KOELN, DOOR, 0)
    toom.set_stock(TROISDORF, DOOR, 0)
    res = await call(mcp, "find_article_nearby", near="Köln", sku=DOOR)
    assert res["nearest_complete"]["name"] == "Bergheim-Zieverich"
    assert res["nearest_complete"]["distance_km"] == pytest.approx(21.3, abs=0.2)
    assert [m["name"] for m in res["markets"]] == [
        "Bergheim-Zieverich", "Langenfeld", "Grevenbroich", "Düren", "Jülich", "Remagen", "Gummersbach",
        "Mönchengladbach", "Köln-Zollstock", "Troisdorf",
    ]
    assert [m["complete"] for m in res["markets"]] == [True] * 8 + [False, False]


async def test_find_article_nothing_available_anywhere(mcp, toom):
    for mid in (KOELN, TROISDORF):
        toom.set_stock(mid, DOOR, 0)
    res = await call(mcp, "find_article_nearby", near="Köln", sku=DOOR, radius_km=20)
    assert res["nearest_complete"] is None
    assert [m["name"] for m in res["markets"]] == ["Köln-Zollstock", "Troisdorf"]  # nearest first when none complete
    assert not any(m["complete"] for m in res["markets"])


async def test_find_article_several_items(mcp, toom):
    toom.set_stock(KOELN, B, 0)  # Köln has A but not B
    toom.set_stock(TROISDORF, A, 1)  # Troisdorf has B and only 1 of 3 A
    res = await call(mcp, "find_article_nearby", near="Köln", radius_km=20,
                     items=[{"sku": A, "quantity": 3}, {"sku": B}])
    assert [a["sku"] for a in res["articles"]] == [A, B]
    assert [a["quantity"] for a in res["articles"]] == [3, 1]
    assert res["nearest_complete"] is None
    k, t = res["markets"]
    assert k["name"] == "Köln-Zollstock" and k["complete"] is False
    assert k["stock"] == {A: 3, B: 0}
    assert [(s["sku"], s["wanted"], s["available"]) for s in k["short"]] == [(B, 1, 0)]
    assert t["name"] == "Troisdorf" and t["complete"] is False
    assert t["stock"] == {A: 1, B: 1}
    assert [(s["sku"], s["wanted"], s["available"]) for s in t["short"]] == [(A, 3, 1)]
    assert k["short"][0]["title"] and k["short"][0]["title"] == res["articles"][1]["title"]
    # one stock request per market for all items
    assert len(stock_requests(toom)) == 2


async def test_find_article_items_complete_in_one_market(mcp, toom):
    toom.set_stock(KOELN, B, 0)
    res = await call(mcp, "find_article_nearby", near="Köln", radius_km=20, items=[{"sku": A}, {"sku": B}])
    assert res["nearest_complete"]["name"] == "Troisdorf"
    assert [m["name"] for m in res["markets"]] == ["Troisdorf", "Köln-Zollstock"]


async def test_find_article_max_markets_caps_checks(mcp, toom):
    res = await call(mcp, "find_article_nearby", near="Köln", sku=DOOR, max_markets=3)
    assert [m["name"] for m in res["markets"]] == KOELN_ORDER[:3]
    assert res["checked"] == 3 and res["markets_in_radius"] == 10
    assert len(res["not_checked"]) == 7
    assert [n.rsplit(" (", 1)[0] for n in res["not_checked"]] == KOELN_ORDER[3:]
    assert res["not_checked"][0] == "Langenfeld (24 km)"
    assert len(stock_requests(toom)) == 3


async def test_find_article_failing_market_gets_error_entry(mcp, toom, monkeypatch):
    break_stock(toom, monkeypatch, KOELN)
    res = await call(mcp, "find_article_nearby", near="Köln", sku=DOOR, radius_km=25)
    by_name = {m["name"]: m for m in res["markets"]}
    broken = by_name["Köln-Zollstock"]
    assert broken["complete"] is False and "HTTP 503" in broken["error"]
    assert "stock" not in broken
    assert by_name["Troisdorf"]["complete"] is True and by_name["Troisdorf"]["stock"] == {DOOR: 1}
    assert by_name["Bergheim-Zieverich"]["complete"] is True
    assert res["nearest_complete"]["name"] == "Troisdorf"
    assert res["markets"][-1]["name"] == "Köln-Zollstock"  # incomplete sorted last


async def test_find_article_unknown_sku_fails_before_any_stock_request(mcp, toom):
    err = await call_err(mcp, "find_article_nearby", near="Köln", sku="9999999")
    assert "No article with the number '9999999'" in err
    assert stock_requests(toom) == []
    err = await call_err(mcp, "find_article_nearby", near="Köln", items=[{"sku": A}, {"sku": "9999999"}])
    assert "No article with the number '9999999'" in err
    assert stock_requests(toom) == []


async def test_find_article_no_market_in_radius(mcp, toom):
    err = await call_err(mcp, "find_article_nearby", near="50.94,6.96", sku=DOOR, radius_km=2)
    assert "No toom market within 2 km of '50.94,6.96'" in err
    assert stock_requests(toom) == []


async def test_find_article_unplaceable_near(mcp, toom):
    assert "Could not place 'Atlantis'" in await call_err(mcp, "find_article_nearby", near="Atlantis", sku=DOOR)


async def test_find_article_validation_errors(mcp, toom):
    assert "Give sku or items" in await call_err(mcp, "find_article_nearby", near="Köln")
    assert "quantity must be >= 1" in await call_err(mcp, "find_article_nearby", near="Köln", sku=DOOR, quantity=0)
    assert "quantity must be >= 1" in await call_err(
        mcp, "find_article_nearby", near="Köln", items=[{"sku": A, "quantity": 0}])
    assert 'each item needs "sku"' in await call_err(mcp, "find_article_nearby", near="Köln", items=[{"quantity": 2}])
    for bad in (0, 41, -1):
        assert "max_markets must be between 1 and 40" in await call_err(
            mcp, "find_article_nearby", near="Köln", sku=DOOR, max_markets=bad)
    for bad in (0, 301):
        assert "radius_km must be between 0 and 300" in await call_err(
            mcp, "find_article_nearby", near="Köln", sku=DOOR, radius_km=bad)
    assert stock_requests(toom) == []


async def test_find_article_max_markets_boundaries_are_valid(mcp, toom):
    assert (await call(mcp, "find_article_nearby", near="Köln", sku=DOOR, max_markets=1))["checked"] == 1
    assert (await call(mcp, "find_article_nearby", near="Köln", sku=DOOR, max_markets=40))["checked"] == 10


async def test_nearby_tools_never_change_a_basket(mcp, toom):
    await call(mcp, "set_market", market="Prenzlau")
    await call(mcp, "get_article", sku=DOOR, also_check=["Köln-Zollstock"])
    await call(mcp, "markets_nearby", near="Köln")
    await call(mcp, "find_article_nearby", near="Köln", items=[{"sku": A, "quantity": 2}, {"sku": B}])
    await call(mcp, "find_article_nearby", near="Köln", sku=DOOR, max_markets=2)
    assert basket_writes(toom) == []
    assert toom.baskets == {}
