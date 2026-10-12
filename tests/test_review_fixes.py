"""Regression tests for the second review round and the struck-through price bug."""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from conftest import call, call_err
from toomy import fit, hours, server
from toomy.client import (
    Market,
    ToomError,
    clean_html,
    extract_dimensions,
    parse_basket,
    parse_measure,
    parse_recommendations,
    parse_search_payload,
)

BERLIN = ZoneInfo("Europe/Berlin")


def search_page(products: list[dict]) -> str:
    payload = {"products": {"list": products, "spaList": [], "count": len(products)}, "filters": []}
    chunk = json.dumps(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), ensure_ascii=False)[1:-1]
    return f'<script>self.__next_f.push([1,"{chunk}"])</script>'


def card(sku, value, special="", saving="", **flags):
    return {
        "sku": sku, "headline": f"Artikel {sku}", "brand": {"text": "X"}, "link": {"url": f"/p/x/{sku}"},
        "price": {"value": value, "special": special, "saving": saving, "paket": "", "loyaltyCardPrice": ""},
        "flags": {"aktion": False, **flags}, "availabilityCode": "0|1", "ratingData": {},
    }


# ---- struck-through prices: the price to pay is `special`, `value` is the old one -------------------------

def test_struck_through_price_reports_what_you_pay_and_what_it_was():
    res = parse_search_payload(search_page([card("1", "799,00 €", "249,99 €", "- 68 %"), card("2", "10,00 €")]))
    a, b = res["products"]
    assert a["price_eur"] == 249.99 and a["price"] == "249,99 €"
    assert a["was_price_eur"] == 799.0 and a["saving"] == "- 68 %"
    assert b["price_eur"] == 10.0 and b["was_price_eur"] is None and b["saving"] is None


def test_undefined_special_is_not_a_discount():
    res = parse_search_payload(search_page([card("1", "5,00 €", "$undefined", "$undefined")]))
    assert res["products"][0]["price_eur"] == 5.0 and res["products"][0]["was_price_eur"] is None


# ---- measures: German numbers ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("87,5 cm", ("length", 87.5)),
    ("1.200,5 mm", ("length", 120.05)),   # thousands dot + decimal comma
    ("1.234.567 g", ("mass", 1234.567)),
    ("1.200 mm", None),                   # one dot + 3 digits: 1200 or 1.2 -> not guessed
    ("2.500 g", None),
    ("0.75 m", ("length", 75.0)),         # a dot with other than 3 digits is a decimal point
    ("12.5 kg", ("mass", 12.5)),
])
def test_german_number_formats(text, expected):
    assert parse_measure(text) == expected


def test_ambiguous_bare_number_with_unit_label_is_not_guessed():
    assert extract_dimensions({"masse": {"Breite (mm)": "1.200"}}) == {}
    assert extract_dimensions({"masse": {"Breite (mm)": "1200"}}) == {"width_cm": 120.0}


# ---- labels: exact only ----------------------------------------------------------------------------------

def test_qualified_labels_and_packaging_groups_do_not_shadow_the_real_size():
    chars = {
        "weitere-eigenschaften": {"Länge Kabel (m)": "5 m", "Höhe verstellbar": "70 cm", "Breite Verpackung": "10 cm"},
        "verpackung": {"Breite": "120 cm", "Gewicht": "40 kg"},
        "masse-gewicht": {"Länge": "200 cm", "Höhe": "100 cm", "Breite (cm)": "60", "Gewicht in kg": "12,5"},
    }
    assert extract_dimensions(chars) == {"length_cm": 200.0, "height_cm": 100.0, "width_cm": 60.0, "weight_kg": 12.5}


def test_a_packaging_size_is_never_taken_as_the_article_size():
    assert extract_dimensions({"verpackung": {"Breite": "120 cm", "Gewicht": "40 kg"}}) == {}
    assert extract_dimensions({"versandmasse": {"Höhe": "30 cm"}, "paketdaten": {"Länge": "50 cm"}}) == {}


def test_a_weight_label_never_takes_a_length():
    assert extract_dimensions({"m": {"Gewicht": "5 cm", "Breite": "2 kg"}}) == {}


# ---- html -------------------------------------------------------------------------------------------------

def test_clean_html_keeps_escaped_angle_brackets_and_normalises_spaces():
    assert clean_html("1 &lt; 2 and 3 &gt; 2") == "1 < 2 and 3 > 2"
    assert clean_html("<p>Bus&nbsp;7<br/> Haltestelle BEZ</p>") == "Bus 7\nHaltestelle BEZ"
    assert clean_html("Farb\xadmisch\xadservice") == "Farbmischservice"
    assert clean_html(None) == ""


# ---- odd answers ------------------------------------------------------------------------------------------

def test_recommendation_with_odd_review_count_is_tolerated():
    data = {"hits": [{"id": "1", "values": {"productNumber": "1", "ratingTotalReviewsCount": "12.0", "ratingOverallMark": "n/a"}},
                     {"id": "2", "values": {"productNumber": "2", "ratingTotalReviewsCount": "n/a"}}]}
    items = parse_recommendations(data)
    assert [i["reviews"] for i in items] == [12, 0] and items[0]["rating"] is None


@pytest.mark.parametrize("bad", [None, [], "x", {"lineItems": [{"product": {}}]}, {"lineItems": [{"quantity": "?"}]}])
def test_basket_with_unexpected_shape_is_a_toomerror(bad):
    with pytest.raises(ToomError, match="unexpected shape"):
        parse_basket(bad)


# ---- opening hours: midnight -----------------------------------------------------------------------------

def _market(h):
    return Market(1, "X", "c", "1", "", "", "", hours=(h,) * 7)


def test_closing_at_midnight_means_end_of_day():
    m = _market(("08:00", "00:00"))
    late = hours.status(m, datetime(2026, 10, 12, 22, 0, tzinfo=BERLIN))
    assert late["open_now"] is True and late["closes_at"] == "00:00"
    early = hours.status(m, datetime(2026, 10, 12, 7, 0, tzinfo=BERLIN))
    assert early["open_now"] is False and early["next_open"]["date"] == "2026-10-12"
    assert hours.status(_market(("08:00", "24:00")), datetime(2026, 10, 12, 23, 30, tzinfo=BERLIN))["open_now"] is True


def test_unreadable_times_count_as_closed_and_never_crash():
    st = hours.status(_market(("soon", "later")), datetime(2026, 10, 12, 10, 0, tzinfo=BERLIN))
    assert st["open_now"] is False and st["next_open"] is None


def test_exact_opening_minute_is_open_and_exact_closing_minute_is_closed():
    m = _market(("08:00", "20:00"))
    assert hours.status(m, datetime(2026, 10, 12, 8, 0, tzinfo=BERLIN))["open_now"] is True
    assert hours.status(m, datetime(2026, 10, 12, 20, 0, tzinfo=BERLIN))["open_now"] is False


# ---- fit: soundness --------------------------------------------------------------------------------------

def test_unlimited_axis_takes_the_longest_side():
    assert fit.check({"width_cm": 120, "height_cm": 30, "thickness_cm": 2}, 100, 50)["fits"] is True
    assert fit.check({"length_cm": 200, "width_cm": 20, "thickness_cm": 2}, 50, 60)["fits"] is True


def test_two_known_sides_cannot_be_certain_when_the_third_could_be_large():
    r = fit.check({"width_cm": 120, "height_cm": 30}, 100, 50)
    assert r["fits"] is None and r["verdict"] == "unklar"


def test_without_rotation_width_and_height_must_be_known_to_say_it_fits():
    assert fit.check({"length_cm": 10, "thickness_cm": 10}, 5, 5, allow_rotation=False)["fits"] is None
    assert fit.check({"width_cm": 10, "thickness_cm": 2}, 50, 5, allow_rotation=False)["fits"] is None
    assert fit.check({"width_cm": 10, "height_cm": 4}, 50, 5, allow_rotation=False)["fits"] is True
    assert fit.check({"width_cm": 60, "height_cm": 4}, 50, 5, allow_rotation=False)["fits"] is False


def test_a_known_side_that_cannot_fit_anywhere_is_a_definite_no():
    assert fit.check({"width_cm": 300}, 100, 100, 100)["fits"] is False  # even with every other side unknown


# ---- rating prior -----------------------------------------------------------------------------------------

def test_one_perfect_review_never_beats_many_good_ones_whatever_the_page_looks_like():
    page = [{"sku": f"p{i}", "rating": 5.0, "reviews": 3} for i in range(5)]  # a page of excellent articles
    ranked = server._rank_by_rating(page + [{"sku": "one", "rating": 5.0, "reviews": 1}, {"sku": "many", "rating": 4.8, "reviews": 200}])
    order = [p["sku"] for p in ranked]
    assert order.index("many") < order.index("one")


# ---- env and kinds -----------------------------------------------------------------------------------------

def test_env_list_uses_semicolon_and_newline_only(monkeypatch):
    monkeypatch.setenv("X_LIST", "toom Berlin, 2. Markt; 10115,10117\n3646")
    assert server._env_list("X_LIST") == ["toom Berlin, 2. Markt", "10115,10117", "3646"]


async def test_get_related_validates_the_kind_before_any_request(mcp, toom):
    await call(mcp, "set_market", market="Prenzlau")
    assert "kind must be one of" in await call_err(mcp, "get_related", sku="1490300", kind="bogus")
    assert not [r for r in toom.requests if r[1].startswith(("/s/", "/p/"))]


async def test_delivery_info_says_what_shipping_was_actually_charged(mcp, toom):
    d = (await call(mcp, "delivery_info", sku="110013", market="Prenzlau"))["delivery"]
    assert d["goods_eur"] >= d["free_shipping_from_eur"] and d["shipping_eur"] == 39.95  # freight pays above the limit
    assert d["shipping_free"] is False and "free_shipping_reached" not in d
