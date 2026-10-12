"""Parsing and matching helpers, against recorded real shapes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from toomy.client import Market, ToomError, _price_to_float, decode_availability, match_markets, parse_basket, parse_product_page, parse_search_payload

FIX = Path(__file__).parent / "fixtures"


def markets() -> list[Market]:
    return [Market.from_api(m) for m in json.loads((FIX / "markets.json").read_text(encoding="utf-8"))["markets"]]


def test_price_parsing():
    assert _price_to_float("119,99 €") == 119.99
    assert _price_to_float("1.234,50\xa0€") == 1234.5
    assert _price_to_float("") is None and _price_to_float(None) is None


def test_search_payload_is_parsed_from_the_real_page_shape():
    res = parse_search_payload((FIX / "search.html").read_text(encoding="utf-8"))
    assert res["count"] == 488  # total hits; only the first page is ever read
    first = res["products"][0]
    assert first["sku"] == "1490300"
    assert first["brand"] == "Bosch"
    assert first["price_eur"] == 119.99
    assert first["url"].startswith("https://toom.de/p/") and first["url"].endswith("/1490300")
    assert first["rating"] == 4.9 and first["reviews"] == 231
    assert res["products"][2]["rating"] is None  # unrated article ("$undefined" in the payload)


def test_search_payload_without_embedded_data_fails_loudly():
    with pytest.raises(ToomError, match="changed its frontend"):
        parse_search_payload("<html>no payload</html>")


def test_search_payload_without_products_is_just_empty():
    html = '<script>self.__next_f.push([1,"{\\"nothing\\":true}"])</script>'
    assert parse_search_payload(html) == {"count": 0, "products": []}


def test_basket_parsing_from_a_real_basket():
    raw = json.loads((FIX / "basket_template.json").read_text(encoding="utf-8"))["basket"]
    b = parse_basket(raw)
    assert b["total_quantity"] == 2 and b["total_eur"] == 239.98
    (line,) = b["lines"]
    assert line["sku"] == "1490300" and line["quantity"] == 2
    assert line["delivery_type"] == "marketPickup_marketPickup"
    assert line["pickup"]["status"] == "Verfügbar" and "Troisdorf" in line["pickup"]["text"]
    assert line["unit_price_eur"] == 119.99


@pytest.mark.parametrize("query, expected", [
    ("3646", ["Berlin-Friedrichshain"]),  # market id
    ("17291", ["Prenzlau"]),  # full postal code
    ("130", ["Berlin-Weißensee"]),  # postal code prefix
    ("weissensee", ["Berlin-Weißensee"]),  # umlaut/ß-insensitive
    ("WEIßENSEE", ["Berlin-Weißensee"]),
    ("magdeburg brückfeld", ["Magdeburg-Brückfeld"]),  # several words
    ("nowhere", []),
])
def test_match_markets(query, expected):
    assert [m.name for m in match_markets(markets(), query)] == expected


def test_match_markets_returns_all_candidates_for_a_vague_query():
    assert {m.name for m in match_markets(markets(), "berlin")} == {"Berlin-Friedrichshain", "Berlin-Weißensee"}


def test_match_markets_falls_back_to_street_names_only_when_name_and_city_match_nothing():
    ms = markets()
    assert [m.name for m in match_markets(ms, "zeppelinstrasse")] == ["Radolfzell"]  # street: Zeppelinstraße 6
    assert match_markets(ms, "radolfzell")[0].name == "Radolfzell"  # name still wins when it matches


def test_market_public_view_has_address_and_url():
    m = next(m for m in markets() if m.name == "Prenzlau")
    pub = m.public()
    assert pub["id"] == 3839 and "17291 Prenzlau" in pub["address"] and pub["url"].startswith("https://toom.de/")


@pytest.mark.parametrize("code, delivery, reservable, orderable", [
    ("0|1|2", True, True, True),
    ("0|1", True, True, False),
    ("0|2", True, False, True),  # e.g. a door: not in stock in the market, "Bestellbar in <Markt>", ships home
    ("1|2", False, True, True),
    ("0", True, False, False),
    ("1", False, True, False),
    ("2", False, False, True),
    ("unavailable", False, False, False),
    (None, False, False, False),
    ("", False, False, False),
])
def test_decode_availability_like_the_toom_product_page(code, delivery, reservable, orderable):
    assert decode_availability(code) == {"home_delivery": delivery, "market_reservable": reservable, "market_orderable": orderable}


def test_search_results_carry_the_decoded_availability():
    first = parse_search_payload((FIX / "search.html").read_text(encoding="utf-8"))["products"][0]
    assert first["availability_code"] == "0|1"
    assert first["availability"] == {"home_delivery": True, "market_reservable": True, "market_orderable": False}


def test_product_page_title_prefers_h1_and_decodes_entities():
    html_text = "<title>Hörmann Tür &#039;T30&#039; | toom Baumarkt</title><h1 class=x>Tür &#039;T30&#039; <b>87,5</b> x 200 cm</h1>"
    assert parse_product_page(html_text) == "Tür 'T30' 87,5 x 200 cm"


def test_product_page_title_falls_back_to_og_title_then_title_tag():
    assert parse_product_page('<meta property="og:title" content="Akku &amp; Co">') == "Akku & Co"
    assert parse_product_page("<title>Hammer 500 g | toom Baumarkt</title>") == "Hammer 500 g"
    assert parse_product_page("<html>nothing</html>") is None
