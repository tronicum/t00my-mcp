"""Regression tests for defects found in review: odd or hostile answers from toom.de and odd tool input."""

from __future__ import annotations

import copy
import json
import re
from urllib.parse import quote

import httpx
import pytest

from conftest import ARTICLE_STATE, DOOR, call, call_err
from t00my_mcp import client as toom_client
from t00my_mcp import geo, server
from t00my_mcp.client import Market, ToomError, fetch_markets, parse_article_state

STOCKS = "/shop/rest/V1/toom/stocks/availability"


def page(state) -> str:
    return f'<div id="root" data-props="{quote(json.dumps(state), safe="-_.!~*()")}"></div>'


def wrap(toom, monkeypatch, override):
    """Route requests through `override(request) -> Response | None`; None falls through to the fake."""
    def handler(request: httpx.Request) -> httpx.Response:
        return override(request) or toom.handler(request)

    monkeypatch.setattr(server, "_transport", httpx.MockTransport(handler))


def market_of(request: httpx.Request) -> int | None:
    m = re.search(r"market_id=(\d+)", request.headers.get("cookie", ""))
    return int(m.group(1)) if m else None


# ---- 1: an HTML answer with status 200 must not abort a multi-market tool ------------------------------

async def test_stock_answer_that_is_not_json_becomes_a_toomerror():
    t = toom_client.Toom(
        Market(1, "A", "c", "1", "", "", "", 50.0, 7.0),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>blocked</html>")),
    )
    with pytest.raises(ToomError, match="not JSON"):
        await t.stock([{"sku": "1", "quantity": 1}])
    with pytest.raises(ToomError, match="not JSON"):
        await t.basket()


async def test_stock_answer_with_unexpected_shape_becomes_a_toomerror():
    t = toom_client.Toom(
        Market(1, "A", "c", "1", "", "", "", 50.0, 7.0),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[{"nope": 1}])),
    )
    with pytest.raises(ToomError, match="unexpected shape"):
        await t.stock([{"sku": "1", "quantity": 1}])


async def test_nearby_search_survives_one_market_answering_html(mcp, toom, monkeypatch):
    wrap(toom, monkeypatch, lambda r: httpx.Response(200, text="<html>blocked</html>") if r.url.path == STOCKS and market_of(r) == 3464 else None)
    res = await call(mcp, "find_article_nearby", near="Köln", sku=DOOR, radius_km=20)
    rows = {r["name"]: r for r in res["markets"]}
    assert "error" in rows["Köln-Zollstock"] and not rows["Köln-Zollstock"]["complete"]
    assert rows["Troisdorf"]["complete"]  # the other market is still answered


async def test_get_article_survives_one_market_answering_html(mcp, toom, monkeypatch):
    wrap(toom, monkeypatch, lambda r: httpx.Response(200, text="<html>x</html>") if r.url.path == STOCKS and market_of(r) == 3382 else None)
    res = await call(mcp, "get_article", sku=DOOR, market="Prenzlau", also_check=["Weißensee"])
    assert res["stock"]["Prenzlau"] == 1 and str(res["stock"]["Berlin-Weißensee"]).startswith("unknown")


# ---- 2: odd product-page state --------------------------------------------------------------------------

@pytest.mark.parametrize("state", [
    [1, 2],
    None,
    {"content": None},
    {"content": []},
    {"content": {}},
    {"content": {"basic_info": "x"}},
    {"content": {"basic_info": {}}},  # neither sku nor title
])
def test_unusable_article_state_is_a_toomerror(state):
    with pytest.raises(ToomError, match="could not be read|no embedded data"):
        parse_article_state(page(state))


def test_oddly_typed_optional_parts_are_tolerated():
    s = copy.deepcopy(ARTICLE_STATE)
    c = s["content"]
    c["details"]["brand"] = "Hörmann"  # a string instead of {"name": ...}
    c["details"]["selling_points"] = ["a", "b"]  # a list instead of {"items": [...]}
    c["characteristics"]["weitere-eigenschaften"] = ["nonsense", {"label": "Marke", "value": "X"}]
    c["characteristics"]["herstellerinformationen"] = ["list instead of dict"]
    c["gallery"] = ["not-a-dict", {"src": "https://static.toom.de/x.png"}]
    c["breadcrumb"] = ["x", {"name": "Haus"}]
    art = parse_article_state(page(s))
    assert art["brand"] is None and art["summary"] == []
    assert art["characteristics"]["weitere-eigenschaften"] == {"Marke": "X"}
    assert art["manufacturer"] is None
    assert art["images"] == ["https://static.toom.de/x.png"] and art["categories"] == ["Haus"]


# ---- 3 + 4: malformed markets and coordinates ------------------------------------------------------------

def test_market_with_null_fields_is_still_usable():
    m = Market.from_api({"id": 7, "name": "X", "link": None, "phone": None, "address": {"zip": None, "city": None, "street": None}})
    assert (m.zip, m.city, m.street, m.phone) == ("", "", "", "") and m.link.endswith("toom.de/")
    assert Market.from_api({"id": 8, "name": "Y", "address": {"zip": 50667}}).zip == "50667"  # a JSON number


@pytest.mark.parametrize("lat, lon", [("inf", "7"), ("nan", "7"), ("200", "7"), ("50", "999"), ("0", "0"), (None, None), ("x", "y")])
def test_invalid_coordinates_mean_no_location(lat, lon):
    m = Market.from_api({"id": 1, "name": "X", "address": {"latitude": lat, "longitude": lon}})
    assert m.lat is None and m.lon is None
    assert geo.within([m], 50.0, 7.0, 1000) == []  # and nothing crashes


async def test_one_malformed_market_does_not_drop_the_list():
    good = {"id": 1, "name": "Good", "address": {"zip": "50667", "latitude": "50.9", "longitude": "6.9"}}
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"markets": [good, {"id": 2}, "garbage", None, {**good, "id": 3, "name": "Also good"}]}))
    assert [m.name for m in await fetch_markets(transport)] == ["Good", "Also good"]
    with pytest.raises(ToomError, match="unexpected shape"):
        await fetch_markets(httpx.MockTransport(lambda r: httpx.Response(200, json={"markets": "nope"})))


# ---- 5: limits --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("limit", [0, -1, -2, 101])
async def test_limits_are_validated(mcp, toom, limit):
    await call(mcp, "set_market", market="Prenzlau")
    assert "limit must be between" in await call_err(mcp, "search_products", query="x", limit=limit)
    assert "limit must be between" in await call_err(mcp, "markets_nearby", near="Köln", limit=limit)


async def test_search_argument_edge_cases(mcp, toom):
    await call(mcp, "set_market", market="Prenzlau")
    assert "must not be empty" in await call_err(mcp, "search_products", query="   ")
    assert "must not be empty" in await call_err(mcp, "search_filters", query="")
    assert "min_price" in await call_err(mcp, "search_products", query="x", min_price=-5)
    assert "max_price" in await call_err(mcp, "search_products", query="x", max_price=-1)
    assert "instead of" in await call_err(mcp, "search_products", query="x", availability="in_market", filters={"pickupInStore": ["1"]})


# ---- 6 + 10: items ----------------------------------------------------------------------------------------

async def test_duplicate_items_are_merged_by_summing(mcp, toom):
    res = await call(mcp, "find_article_nearby", near="Köln", items=[{"sku": DOOR, "quantity": 1}, {"sku": DOOR, "quantity": 9}], radius_km=5)
    assert res["articles"] == [{"sku": DOOR, "title": res["articles"][0]["title"], "quantity": 10}]
    assert [r for r in toom.requests if r[1] == STOCKS]  # asked once per market, not once per duplicate
    assert len([r for r in toom.requests if r[1] == STOCKS]) == 1


async def test_too_many_items_are_refused_before_any_request(mcp, toom):
    items = [{"sku": str(i), "quantity": 1} for i in range(11)]
    assert "At most 10 articles" in await call_err(mcp, "find_article_nearby", near="Köln", items=items)
    assert toom.requests == []


@pytest.mark.parametrize("bad", [0, -3, 2.7, "abc", True, [], {}])
async def test_bad_quantities_are_a_toolerror_not_a_crash(mcp, toom, bad):
    items = [{"sku": DOOR, "quantity": bad}]
    assert "whole number >= 1" in await call_err(mcp, "find_article_nearby", near="Köln", items=items)
    assert "whole number >= 1" in await call_err(mcp, "compare_markets", markets=["Prenzlau"], items=items)


async def test_quantity_given_as_null_or_text_number_is_understood(mcp, toom):
    res = await call(mcp, "find_article_nearby", near="Köln", items=[{"sku": DOOR, "quantity": None}], radius_km=5)
    assert res["articles"][0]["quantity"] == 1
    res = await call(mcp, "find_article_nearby", near="Köln", items=[{"sku": DOOR, "quantity": "3"}], radius_km=5)
    assert res["articles"][0]["quantity"] == 3


# ---- 7: also_check ----------------------------------------------------------------------------------------

async def test_also_check_is_capped_and_resolved_before_the_article_request(mcp, toom):
    assert "at most 10" in await call_err(mcp, "get_article", sku=DOOR, market="Prenzlau", also_check=["Prenzlau"] * 11)
    err = await call_err(mcp, "get_article", sku=DOOR, market="Prenzlau", also_check=["Atlantis"])
    assert "No toom market found" in err
    assert not [r for r in toom.requests if r[1].startswith("/s/") or r[1].startswith("/p/")]  # no article fetched


# ---- 8: sku types -----------------------------------------------------------------------------------------

async def test_numeric_sku_in_the_page_state_is_normalised(mcp, toom, monkeypatch):
    state = copy.deepcopy(ARTICLE_STATE)
    state["content"]["basic_info"]["sku"] = int(DOOR)  # a JSON number instead of a string

    def numeric(request: httpx.Request):
        if request.url.path.startswith("/p/"):
            return httpx.Response(200, text=f'<html><body><div id="root" data-props="{quote(json.dumps(state), safe="-_.!~*()")}"></div></body></html>')

    wrap(toom, monkeypatch, numeric)
    res = await call(mcp, "get_article", sku=DOOR, market="Prenzlau")
    assert res["article"]["sku"] == DOOR and isinstance(res["article"]["sku"], str)
    assert res["stock"]["Prenzlau"] == 1  # the market has 10, capped at the asked quantity; not 0 because of int vs str


# ---- 9: where is "near" ------------------------------------------------------------------------------------

M = [
    Market(1, "Köln-Zollstock", "Köln", "50969", "", "", "", 50.90, 6.93),
    Market(2, "Hagen", "Hagen", "58095", "", "", "", 51.36, 7.47),
    Market(3, "Hagenow", "Hagenow", "19230", "", "", "", 53.43, 11.19),
    Market(4, "Köln-Porz", "Köln", "51145", "", "", "", 50.88, 7.06),
]


def test_postal_code_with_a_city_name_is_understood_either_way():
    for q in ("50969 Köln", "Köln 50969", "50969 Köln-Zollstock"):
        lat, lon, label = geo.resolve_center(M, q)
        assert (lat, lon) == (50.90, 6.93) and "postal code 50969" in label


def test_german_decimal_comma_is_not_taken_as_coordinates():
    with pytest.raises(ValueError):
        geo.resolve_center(M, "50,94")
    assert geo.resolve_center(M, "50.94,6.96")[2].startswith("coordinates")


def test_word_hits_far_apart_are_reported_as_ambiguous_not_averaged():
    with pytest.raises(ValueError, match="far apart") as e:
        geo.resolve_center(M, "age")  # parts of words: Hagen + Hagenow
    assert "Hagen" in str(e.value) and "Hagenow" in str(e.value)


def test_whole_word_match_beats_a_part_of_a_word():
    lat, lon, label = geo.resolve_center(M, "Köln")
    assert 50.8 < lat < 51.0 and "2 market(s)" in label  # both Köln markets, close together
    assert geo.resolve_center(M, "Hagen")[2] == "market Hagen"  # exact name wins over Hagenow


def test_single_digit_postal_prefix_is_explained():
    with pytest.raises(ValueError, match="at least 2 digits"):
        geo.resolve_center(M, "5")
