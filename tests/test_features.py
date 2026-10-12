"""Newer features: opening hours, market_info, measures/dimensions, fits_space, compare_articles,
delivery_info, get_related, best_rated search and the TOOMY_DEFAULT_MARKET / TOOMY_HOME_MARKETS defaults."""

from __future__ import annotations

import copy
import json
import re
from datetime import date, datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
import pytest

import conftest
from conftest import ARTICLE_STATE, DOOR, FIX, call, call_err
from toomy import fit, hours, server
from toomy.client import Market, clean_html, extract_dimensions, parse_measure

BERLIN = ZoneInfo("Europe/Berlin")
A, B, C, D = "1490300", "10458992", "1501938", "1501286"
E5, F6 = "10459289", "1753247"
FRIEDRICHSHAIN, WEISSENSEE, PRENZLAU, KOELN, TROISDORF = 3646, 3382, 3839, 3464, 3248
ALBSTADT = 3340
STOCKS = "/shop/rest/V1/toom/stocks/availability"


def at(y, mo, d, h=12, mi=0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=BERLIN)


def market(mid: int) -> Market:
    raw = json.loads((FIX / "markets.json").read_text(encoding="utf-8"))
    raw = raw["markets"] if isinstance(raw, dict) else raw
    return Market.from_api(next(m for m in raw if m["id"] == mid))


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("TOOMY_DEFAULT_MARKET", raising=False)
    monkeypatch.delenv("TOOMY_HOME_MARKETS", raising=False)


def wrap(toom, monkeypatch, override):
    """Route requests through `override(request) -> Response | None`; None falls through to the fake."""
    def handler(request: httpx.Request) -> httpx.Response:
        return override(request) or toom.handler(request)

    monkeypatch.setattr(server, "_transport", httpx.MockTransport(handler))


def market_of(request: httpx.Request) -> int | None:
    m = re.search(r"market_id=(\d+)", request.headers.get("cookie", ""))
    return int(m.group(1)) if m else None


def article_html(sku: str, mutate=None, title: str = "Testartikel") -> str:
    state = copy.deepcopy(ARTICLE_STATE)
    state["content"]["basic_info"].update(sku=sku, name=title)
    state["content"]["meta_data"].update(
        title=title, canonical_url=f"https://toom.de/p/test/{sku}", meta_price=10.0
    )
    if mutate:
        mutate(state)
    enc = quote(json.dumps(state, ensure_ascii=False, separators=(",", ":")), safe="-_.!~*'()")
    return f'<html><head><title>{title} | toom Baumarkt</title></head><body><div id="root" data-props="{enc}"></div><h1>{title}</h1></body></html>'


def serve_articles(toom, monkeypatch, mutators: dict):
    """Answer the product pages of `mutators` ({sku: callable(state)}) with a modified door state."""
    def override(request):
        if request.url.path.startswith("/p/"):
            sku = request.url.path.rstrip("/").rsplit("/", 1)[-1]
            if sku in mutators:
                return httpx.Response(200, text=article_html(sku, mutators[sku]))
        return None

    wrap(toom, monkeypatch, override)


def set_chars(group_items: dict[str, list[tuple[str, str]]]):
    """A mutator replacing the characteristics with {group: [(label, value)]} (hersteller block kept)."""
    def mutate(state):
        old = state["content"]["characteristics"]
        state["content"]["characteristics"] = {
            "herstellerinformationen": old["herstellerinformationen"],
            **{g: [{"code": f"c{i}", "label": l, "value": v} for i, (l, v) in enumerate(items)] for g, items in group_items.items()},
        }
    return mutate


# =============================================================== 1. hours.py

def test_open_inside_hours():
    p = market(PRENZLAU)
    assert at(2026, 10, 12).weekday() == 0  # a Monday
    st = hours.status(p, at(2026, 10, 12, 10, 30))
    assert st == {"open_now": True, "closes_at": "20:00", "today": "07:00–20:00", "today_is_special_day": False, "next_open": None}


def test_closed_before_opening_opens_today():
    st = hours.status(market(PRENZLAU), at(2026, 10, 12, 6, 0))
    assert st["open_now"] is False and st["closes_at"] is None
    assert st["today"] == "07:00–20:00"
    assert st["next_open"] == {"date": "2026-10-12", "weekday": "Montag", "from": "07:00", "until": "20:00"}


def test_closed_after_closing_opens_next_day():
    st = hours.status(market(PRENZLAU), at(2026, 10, 12, 21, 0))
    assert st["open_now"] is False
    assert st["next_open"] == {"date": "2026-10-13", "weekday": "Dienstag", "from": "07:00", "until": "20:00"}


def test_sunday_is_closed_and_next_open_is_monday():
    st = hours.status(market(PRENZLAU), at(2026, 10, 25, 12, 0))
    assert at(2026, 10, 25).weekday() == 6
    assert st["open_now"] is False and st["today"] == "geschlossen" and st["today_is_special_day"] is False
    assert st["next_open"] == {"date": "2026-10-26", "weekday": "Montag", "from": "07:00", "until": "20:00"}


def test_special_closed_day_overrides_weekly_hours():
    st = hours.status(market(PRENZLAU), at(2026, 10, 31, 10, 0))
    assert at(2026, 10, 31).weekday() == 5  # a Saturday, normally 07:00-20:00
    assert st["open_now"] is False and st["closes_at"] is None
    assert st["today"] == "geschlossen" and st["today_is_special_day"] is True
    assert st["next_open"]["date"] == "2026-11-02" and st["next_open"]["weekday"] == "Montag"


def test_next_open_skips_special_day_and_sunday():
    st = hours.status(market(PRENZLAU), at(2026, 10, 30, 21, 0))  # Friday evening
    assert st["next_open"] == {"date": "2026-11-02", "weekday": "Montag", "from": "07:00", "until": "20:00"}


def test_special_opening_on_a_sunday_opens_the_market():
    albstadt = market(ALBSTADT)
    st = hours.status(albstadt, at(2026, 10, 25, 14, 0))
    assert st == {"open_now": True, "closes_at": "18:00", "today": "13:00–18:00", "today_is_special_day": True, "next_open": None}
    nxt = hours.status(albstadt, at(2026, 10, 24, 21, 0))  # Saturday night: next opening is that Sunday
    assert nxt["next_open"] == {"date": "2026-10-25", "weekday": "Sonntag", "from": "13:00", "until": "18:00"}


def test_boundary_minutes():
    p = market(PRENZLAU)
    assert hours.status(p, at(2026, 10, 12, 6, 59))["open_now"] is False
    assert hours.status(p, at(2026, 10, 12, 7, 0))["open_now"] is True
    assert hours.status(p, at(2026, 10, 12, 19, 59))["open_now"] is True
    closing = hours.status(p, at(2026, 10, 12, 20, 0))
    assert closing["open_now"] is False and closing["closes_at"] is None
    assert closing["next_open"]["date"] == "2026-10-13"  # not "today" again


def test_week_is_monday_to_sunday():
    wk = hours.week(market(PRENZLAU))
    assert [d["day"] for d in wk] == ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    assert [d["hours"] for d in wk] == ["07:00–20:00"] * 6 + ["geschlossen"]
    assert hours.week(market(FRIEDRICHSHAIN))[0]["hours"] == "08:00–20:00"


def test_upcoming_special_days_window():
    p = market(PRENZLAU)
    sat = {"date": "2026-10-31", "weekday": "Samstag", "hours": "geschlossen"}
    assert hours.upcoming_special_days(p, date(2026, 10, 12)) == [sat]
    assert hours.upcoming_special_days(p, date(2026, 10, 12), days=19) == [sat]  # inclusive upper bound
    assert hours.upcoming_special_days(p, date(2026, 10, 12), days=18) == []
    assert hours.upcoming_special_days(p, date(2026, 10, 31)) == [sat]  # today counts
    assert hours.upcoming_special_days(p, date(2026, 11, 1)) == []  # in the past
    assert hours.upcoming_special_days(market(WEISSENSEE), date(2026, 10, 12)) == []


def test_upcoming_special_days_are_sorted_and_show_opening_hours():
    got = hours.upcoming_special_days(market(ALBSTADT), date(2026, 10, 12))
    assert got == [
        {"date": "2026-10-25", "weekday": "Sonntag", "hours": "13:00–18:00"},
        {"date": "2026-11-01", "weekday": "Sonntag", "hours": "geschlossen"},
    ]


# ============================================================ 2. market_info

async def test_market_info_prenzlau(mcp, toom, monkeypatch):
    monkeypatch.setattr(server, "_now", lambda: at(2026, 10, 12, 10, 30))
    res = await call(mcp, "market_info", market="Prenzlau")
    assert res["market"]["id"] == PRENZLAU and res["market"]["name"] == "Prenzlau"
    assert res["now"] == "2026-10-12 10:30"
    assert res["status"] == {"open_now": True, "closes_at": "20:00", "today": "07:00–20:00", "today_is_special_day": False, "next_open": None}
    assert [d["day"] for d in res["week"]][0] == "Montag" and res["week"][6] == {"day": "Sonntag", "hours": "geschlossen"}
    assert res["special_days"] == [{"date": "2026-10-31", "weekday": "Samstag", "hours": "geschlossen"}]
    assert "Holzzuschnitt" in res["services"] and "Miettransporterservice" in res["services"]
    assert "Lieferservice" in res["services"]
    for s in res["services"]:
        assert "\xad" not in s and "&shy;" not in s
    assert res["has"] == {
        "rental_van": True, "trailer_rental": True, "wood_cutting": True,
        "tool_rental": True, "delivery_service": True, "pickup_service": True,
    }
    assert res["pickup"]["phone"] == "03984 8327210"
    assert res["pickup"]["click_and_collect"] is True
    assert res["pickup"]["where_in_the_market"].startswith("Im toom Baumarkt Prenzlau befindet sich die Abholstation")
    assert res["pickup"]["parking"] == "An unserem toom Baumarkt stehen ausreichend Parkplätze zur Verfügung."
    assert res["pickup"]["public_transport"].startswith("Bus 403, 431, 432, 435, 447, 466")
    assert "<" not in res["pickup"]["public_transport"] and "Haltestelle Prenzlau Einkaufszentrum" in res["pickup"]["public_transport"]
    assert res["contact"] == {"phone": "03984 832721-0", "email": "Prenzlau@toombm.de"}


async def test_market_info_has_flags_differ_per_market(mcp, toom, monkeypatch):
    monkeypatch.setattr(server, "_now", lambda: at(2026, 10, 12, 10, 30))
    res = await call(mcp, "market_info", market="Weißensee")
    assert res["has"]["rental_van"] is False and res["has"]["trailer_rental"] is False
    assert res["has"]["wood_cutting"] is True and res["has"]["tool_rental"] is True
    assert res["special_days"] == []
    assert res["week"][0]["hours"] == "07:00–20:00"


async def test_market_info_closed_on_the_special_day(mcp, toom, monkeypatch):
    monkeypatch.setattr(server, "_now", lambda: at(2026, 10, 31, 10, 0))
    res = await call(mcp, "market_info", market="Prenzlau")
    assert res["status"]["open_now"] is False and res["status"]["today_is_special_day"] is True
    assert res["status"]["next_open"]["date"] == "2026-11-02"
    assert res["special_days"][0]["date"] == "2026-10-31"


async def test_market_info_uses_selected_market_and_needs_one(mcp, toom, monkeypatch):
    monkeypatch.setattr(server, "_now", lambda: at(2026, 10, 12, 10, 30))
    assert "No market selected" in await call_err(mcp, "market_info")
    await call(mcp, "set_market", market="Troisdorf")
    assert (await call(mcp, "market_info"))["market"]["id"] == TROISDORF


# =================================================== 3. measures / dimensions

@pytest.mark.parametrize(
    "text,default,expected",
    [
        ("87,5 cm", None, ("length", 87.5)),
        ("200 mm", None, ("length", 20.0)),
        ("1,2 m", None, ("length", 120.0)),
        ("1.5 m", None, ("length", 150.0)),
        ("2,5 kg", None, ("mass", 2.5)),
        ("500 g", None, ("mass", 0.5)),
        ("87,5", "cm", ("length", 87.5)),
        (200, "mm", ("length", 20.0)),
        (87.5, "cm", ("length", 87.5)),
        (3, "kg", ("mass", 3.0)),
        ("  12 CM", None, ("length", 12.0)),
    ],
)
def test_parse_measure(text, default, expected):
    assert parse_measure(text, default) == expected


@pytest.mark.parametrize("text", ["n/a", "", None, True, "abc cm", "87,5", "2 Stück", "3 cmx", ["1 cm"], {"a": 1}, "cm 87"])
def test_parse_measure_junk_is_none(text):
    assert parse_measure(text) is None


def test_parse_measure_number_without_unit_is_none():
    assert parse_measure(12) is None
    assert parse_measure("12") is None


def test_extract_dimensions_label_variants():
    chars = {"masse": {"Breite (cm)": "87,5", "Höhe in cm": "200", "Tiefe": "4,5 cm", "Länge (mm)": "1200", "Durchmesser": "30 cm"}}
    assert extract_dimensions(chars) == {
        "width_cm": 87.5, "height_cm": 200.0, "depth_cm": 4.5, "length_cm": 120.0, "diameter_cm": 30.0,
    }


def test_extract_dimensions_ignores_lists_and_unrelated_labels():
    chars = {"g": {"Breite": ["80 cm", "90 cm"], "Farbe": "Grau", "Höhe": "2 m"}}
    assert extract_dimensions(chars) == {"height_cm": 200.0}


def test_extract_dimensions_weight_and_unit_kind_must_match():
    assert extract_dimensions({"g": {"Gewicht": "2,5 kg"}}) == {"weight_kg": 2.5}
    assert extract_dimensions({"g": {"Gewicht (g)": "500"}}) == {"weight_kg": 0.5}
    assert extract_dimensions({"g": {"Gewicht": "87 cm"}}) == {}  # a length is not a weight
    assert extract_dimensions({"g": {"Länge": "2 kg"}}) == {}  # a weight is not a length
    assert extract_dimensions({"g": {"Stärke": "4,5 cm"}}) == {"thickness_cm": 4.5}


def test_extract_dimensions_first_value_wins_across_groups():
    chars = {"a": {"Breite": "10 cm"}, "b": {"Breite": "99 cm"}}
    assert extract_dimensions(chars) == {"width_cm": 10.0}


def test_clean_html():
    assert clean_html("Holz&shy;zuschnitt") == "Holzzuschnitt"
    assert clean_html("Holz\xadzuschnitt") == "Holzzuschnitt"
    assert clean_html("a &amp; b") == "a & b"
    assert clean_html(None) == ""
    assert clean_html("  a   b \t c ") == "a b c"
    assert clean_html("<p>x</p>\r\n<p>y</p>") == "x\ny"
    assert clean_html("<p>Bus 7<br />Haltestelle BEZ</p>") == "Bus 7\nHaltestelle BEZ"
    assert clean_html("<b>fett</b>") == "fett"


# ================================================= 4. fit.check / fits_space

DOOR_DIMS = {"width_cm": 87.5, "height_cm": 200.0, "thickness_cm": 4.5}


def test_fit_door_in_opening():
    r = fit.check(DOOR_DIMS, 90, 210)
    assert r["fits"] is True and r["verdict"] == "passt"
    assert r["article_cm"] == DOOR_DIMS and r["space_cm"] == {"max_width_cm": 90, "max_height_cm": 210}
    assert r["weight_kg"] is None


def test_fit_door_too_narrow():
    r = fit.check(DOOR_DIMS, 80, 210, 10)  # depth limited too: the door cannot be carried through edge-on
    assert r["fits"] is False and r["verdict"] == "passt nicht"
    assert r["reasons"] == ["Eine Seite von 87.5 cm passt nicht in die 80 cm des Platzes (auch gedreht nicht)."]


def test_fit_unlimited_depth_lets_a_flat_article_pass_edge_on():
    """A 87.5 x 200 x 4.5 cm door goes through an 80 cm wide, 210 cm high opening when the depth is open:
    carried upright, its width runs along the (unlimited) depth and only the 4.5 cm thickness uses the 80 cm."""
    r = fit.check(DOOR_DIMS, 80, 210)
    assert r["fits"] is True and r["verdict"] == "passt"
    assert fit.check(DOOR_DIMS, 100, 100)["fits"] is True  # lengthwise into a 100x100 opening of unlimited depth


def test_fit_rotation():
    assert fit.check(DOOR_DIMS, 210, 90)["fits"] is True
    r = fit.check(DOOR_DIMS, 210, 90, allow_rotation=False)
    assert r["fits"] is False and r["reasons"] == ["Höhe 200 cm > 90 cm (ohne Drehen)."]
    assert fit.check(DOOR_DIMS, 90, 210, allow_rotation=False)["fits"] is True
    assert fit.check(DOOR_DIMS, 100, 100, 150)["fits"] is False  # with a limited depth the 200 cm side has no axis


def test_fit_depth_axis():
    assert fit.check(DOOR_DIMS, 90, 210, 5)["fits"] is True
    assert fit.check(DOOR_DIMS, 90, 210, 4)["fits"] is False
    r = fit.check(DOOR_DIMS, 90, 210, 4, allow_rotation=False)
    assert r["fits"] is False and r["reasons"] == ["Tiefe/Länge 4.5 cm > 4 cm (ohne Drehen)."]
    assert fit.check(DOOR_DIMS, 90, 210, 5, allow_rotation=False)["fits"] is True


def test_fit_two_known_dims_in_three_axis_space_is_unclear():
    r = fit.check({"width_cm": 50, "height_cm": 50}, 100, 100, 100)
    assert r["fits"] is None and r["verdict"] == "unklar"
    assert "nicht alle" in r["reasons"][0]  # says that dimensions are missing
    assert fit.check({"width_cm": 50, "height_cm": 50}, 100, 100)["fits"] is True  # 2 axes constrained: enough
    assert fit.check({"width_cm": 150, "height_cm": 50}, 100, 100, 100)["fits"] is False  # too big whatever the rest


def test_fit_without_dimensions_is_unclear():
    r = fit.check({}, 100, 100)
    assert r["fits"] is None and r["verdict"] == "unklar" and r["article_cm"] == {}
    assert r["reasons"] == ["toom states no dimensions for this article."]
    assert fit.check({"weight_kg": 3.0}, 100, 100)["weight_kg"] == 3.0


def test_fit_diameter_counts_twice():
    assert fit.check({"diameter_cm": 30, "height_cm": 100}, 40, 120)["fits"] is True
    assert fit.check({"diameter_cm": 30, "height_cm": 100}, 25, 120)["fits"] is False


@pytest.mark.parametrize("w,h,d", [(0, 10, None), (-5, 10, None), (10, 0, None), (10, 10, 0), (True, 10, None), (10, float("nan"), None), (float("inf"), 10, None)])
def test_fit_invalid_inputs(w, h, d):
    with pytest.raises(ValueError, match="must be a positive number"):
        fit.check(DOOR_DIMS, w, h, d)


async def test_fits_space_tool_door(mcp, toom):
    await call(mcp, "set_market", market="Prenzlau")
    ok = await call(mcp, "fits_space", sku=DOOR, max_width_cm=90, max_height_cm=210)
    assert ok["fits"] is True and ok["verdict"] == "passt"
    assert ok["article"] == {"sku": DOOR, "title": "Feuerschutztür 'T30-1 H8-5' 87,5 x 200 cm", "url": "https://toom.de/p/feuerschutztuer-t30-1-h8-5-875-x-200-cm/110013"}
    assert ok["article_cm"] == DOOR_DIMS
    assert ok["space_cm"] == {"max_width_cm": 90, "max_height_cm": 210}
    assert (await call(mcp, "fits_space", sku=DOOR, max_width_cm=80, max_height_cm=210, max_depth_cm=10))["fits"] is False
    assert (await call(mcp, "fits_space", sku=DOOR, max_width_cm=80, max_height_cm=210))["fits"] is True  # depth open
    assert (await call(mcp, "fits_space", sku=DOOR, max_width_cm=210, max_height_cm=90))["fits"] is True
    assert (await call(mcp, "fits_space", sku=DOOR, max_width_cm=210, max_height_cm=90, allow_rotation=False))["fits"] is False


async def test_fits_space_works_without_a_selected_market(mcp, toom):
    first = json.loads((FIX / "markets.json").read_text(encoding="utf-8"))
    first = (first["markets"] if isinstance(first, dict) else first)[0]["id"]
    res = await call(mcp, "fits_space", sku=DOOR, max_width_cm=90, max_height_cm=210)
    assert res["fits"] is True
    assert toom.queries == [(DOOR, first)]  # any market's page reads the same
    assert "No toom market found" in await call_err(mcp, "fits_space", sku=DOOR, max_width_cm=90, max_height_cm=210, market="atlantis")


async def test_fits_space_unknown_article(mcp, toom):
    assert "No article with the number 'NOPE'" in await call_err(mcp, "fits_space", sku="NOPE", max_width_cm=90, max_height_cm=210)


@pytest.mark.parametrize("args,msg", [
    ({"max_width_cm": 0, "max_height_cm": 210}, "width_cm must be a positive number"),
    ({"max_width_cm": 90, "max_height_cm": -1}, "height_cm must be a positive number"),
    ({"max_width_cm": 90, "max_height_cm": 210, "max_depth_cm": 0}, "depth_cm must be a positive number"),
])
async def test_fits_space_tool_rejects_invalid_space(mcp, toom, args, msg):
    assert msg in await call_err(mcp, "fits_space", sku=DOOR, **args)


async def test_fits_space_tool_bool_is_never_a_fit(mcp, toom):
    # pydantic coerces True to 1.0 cm (or fastmcp refuses it): in no case may the door "fit" into 1 cm
    try:
        res = await call(mcp, "fits_space", sku=DOOR, max_width_cm=True, max_height_cm=210)
    except Exception:
        return
    assert res["fits"] is False


async def test_fits_space_passes_weight_and_two_dims(mcp, toom, monkeypatch):
    serve_articles(toom, monkeypatch, {
        A: set_chars({"masse-gewicht": [("Breite", "60 cm"), ("Höhe", "80 cm"), ("Gewicht", "12,5 kg")]}),
        B: set_chars({"masse-gewicht": [("Breite", "60 cm")]}),
    })
    res = await call(mcp, "fits_space", sku=A, max_width_cm=100, max_height_cm=100, market="Prenzlau")
    assert res["weight_kg"] == 12.5 and res["fits"] is True
    assert res["article_cm"] == {"width_cm": 60.0, "height_cm": 80.0}
    unclear = await call(mcp, "fits_space", sku=A, max_width_cm=100, max_height_cm=100, max_depth_cm=100, market="Prenzlau")
    assert unclear["fits"] is None and unclear["verdict"] == "unklar"
    assert unclear["weight_kg"] == 12.5
    # a single known dimension is not enough for a verdict, however it fits
    single = await call(mcp, "fits_space", sku=B, max_width_cm=100, max_height_cm=100, market="Prenzlau")
    assert single["fits"] is None


async def test_fits_space_article_without_dimensions(mcp, toom, monkeypatch):
    serve_articles(toom, monkeypatch, {A: set_chars({"weitere-eigenschaften": [("Farbe", "Grau")]})})
    res = await call(mcp, "fits_space", sku=A, max_width_cm=100, max_height_cm=100, market="Prenzlau")
    assert res["fits"] is None and res["article_cm"] == {} and res["weight_kg"] is None


def test_fit_board_goes_in_lengthwise_when_depth_is_unlimited():
    r = fit.check({"width_cm": 20, "height_cm": 200, "thickness_cm": 2}, 50, 60)
    assert r["fits"] is True


# ========================================================= 5. compare_articles

def _compare_mutators():
    return {
        A: set_chars({
            "masse-gewicht": [("Breite", "60 cm"), ("Höhe", "200 cm")],
            "weitere-eigenschaften": [("Farbe", "Grau|Weiß"), ("Marke", "Hörmann")],
        }),
        B: set_chars({
            "masse-gewicht": [("Breite", "90 cm"), ("Höhe", "200 cm"), ("Gewicht", "3 kg")],
            "weitere-eigenschaften": [("Farbe", "Grau|Weiß"), ("Marke", "Hörmann")],
        }),
    }


async def test_compare_articles_differs_flag_and_order(mcp, toom, monkeypatch):
    serve_articles(toom, monkeypatch, _compare_mutators())
    res = await call(mcp, "compare_articles", skus=[A, B], market="Prenzlau")
    assert res["market"] == "Prenzlau"
    assert [a["sku"] for a in res["articles"]] == [A, B]
    assert res["articles"][0]["dimensions"] == {"width_cm": 60.0, "height_cm": 200.0}
    assert res["articles"][1]["dimensions"] == {"width_cm": 90.0, "height_cm": 200.0, "weight_kg": 3.0}
    chars = res["characteristics"]
    assert [c["label"] for c in chars] == ["Breite", "Gewicht", "Farbe", "Höhe", "Marke"]
    assert [c["differs"] for c in chars] == [True, True, False, False, False]
    assert chars[0]["values"] == {A: "60 cm", B: "90 cm"}
    assert chars[1]["values"] == {A: None, B: "3 kg"}  # present on one article only counts as different
    assert chars[2]["values"] == {A: ["Grau", "Weiß"], B: ["Grau", "Weiß"]}
    assert res["stock"] == {"Prenzlau": {A: 1, B: 1}}


async def test_compare_articles_count_limits(mcp, toom):
    for skus in ([A], [], [A, B, C, D, E5, F6, DOOR]):
        assert "Give 2 to 6 article numbers." in await call_err(mcp, "compare_articles", skus=skus, market="Prenzlau")
    res = await call(mcp, "compare_articles", skus=[A, B, C, D, E5, F6], market="Prenzlau")
    assert [a["sku"] for a in res["articles"]] == [A, B, C, D, E5, F6]


async def test_compare_articles_duplicates_are_merged(mcp, toom):
    res = await call(mcp, "compare_articles", skus=[A, B, A, f" {B} "], market="Prenzlau")
    assert [a["sku"] for a in res["articles"]] == [A, B]
    assert "different article numbers" in await call_err(mcp, "compare_articles", skus=[A, A], market="Prenzlau")
    assert "different article numbers" in await call_err(mcp, "compare_articles", skus=[A, "  "], market="Prenzlau")


async def test_compare_articles_stock_per_market_with_also_check(mcp, toom):
    toom.set_stock(WEISSENSEE, A, 0)
    toom.set_stock(FRIEDRICHSHAIN, B, 0)
    res = await call(mcp, "compare_articles", skus=[A, B], market="Prenzlau",
                     also_check=["Weißensee", "Friedrichshain", "Prenzlau"])
    assert res["stock"] == {
        "Prenzlau": {A: 1, B: 1},
        "Berlin-Weißensee": {A: 0, B: 1},
        "Berlin-Friedrichshain": {A: 1, B: 0},
    }
    assert list(res["stock"])[0] == "Prenzlau"
    stock_calls = [r for r in toom.requests if r[1] == STOCKS]
    assert sorted(r[2] for r in stock_calls) == sorted([PRENZLAU, WEISSENSEE, FRIEDRICHSHAIN])


async def test_compare_articles_also_check_limit(mcp, toom):
    assert "at most 10" in await call_err(mcp, "compare_articles", skus=[A, B], market="Prenzlau", also_check=["3839"] * 11)


async def test_compare_articles_failing_market_is_unknown_not_fatal(mcp, toom, monkeypatch):
    def override(request):
        if request.url.path == STOCKS and market_of(request) == WEISSENSEE:
            return httpx.Response(500, text="boom")
        return None

    wrap(toom, monkeypatch, override)
    res = await call(mcp, "compare_articles", skus=[A, B], market="Prenzlau", also_check=["Weißensee"])
    assert res["stock"]["Prenzlau"] == {A: 1, B: 1}
    for sku in (A, B):
        v = res["stock"]["Berlin-Weißensee"][sku]
        assert v.startswith("unknown (") and "HTTP 500" in v


async def test_compare_articles_unknown_sku(mcp, toom):
    assert "No article with the number 'NOPE'" in await call_err(mcp, "compare_articles", skus=[A, "NOPE"], market="Prenzlau")


async def test_compare_articles_needs_a_market(mcp, toom):
    assert "No market selected" in await call_err(mcp, "compare_articles", skus=[A, B])


# ============================================================ 6. delivery_info

async def test_delivery_info_freight_is_charged_even_above_the_parcel_threshold(mcp, toom):
    res = await call(mcp, "delivery_info", sku=DOOR, market="Prenzlau")
    assert res["article"]["sku"] == DOOR and res["article"]["price_eur"] == 249.99
    assert res["market"] == "Prenzlau" and res["quantity"] == 1
    d = res["delivery"]
    assert d["possible"] is True
    assert d["lead_time"] == "Lieferzeit: ca. 4-8 Werktage" and d["status"] == "Verfügbar"
    assert d["shipping_eur"] == 39.95 and d["free_shipping_from_eur"] == 59.0  # bulky: freight, not parcel
    assert d["goods_eur"] == 249.99 and d["total_eur"] == pytest.approx(289.94)
    assert d["shipping_free"] is False and "freight" in d["free_shipping_note"]
    p = res["pickup"]
    assert p["in_stock"] == 1 and p["enough"] is True and p["status"] == "Verfügbar" and p["extra_cost_eur"] == 0.0


async def test_delivery_info_charged_shipping_below_threshold(mcp, toom):
    toom.free_shipping_from = 1000
    d = (await call(mcp, "delivery_info", sku=DOOR, market="Prenzlau"))["delivery"]
    assert d["shipping_eur"] == 39.95 and d["free_shipping_from_eur"] == 1000.0
    assert d["goods_eur"] == 249.99 and d["total_eur"] == pytest.approx(289.94)
    assert d["shipping_free"] is False


async def test_delivery_info_quantity_and_stock(mcp, toom):
    toom.set_stock(PRENZLAU, A, 1)
    res = await call(mcp, "delivery_info", sku=A, quantity=2, market="Prenzlau")
    assert res["quantity"] == 2
    assert res["pickup"]["in_stock"] == 1 and res["pickup"]["enough"] is False and res["pickup"]["status"] == "Eingeschränkt"
    d = res["delivery"]
    assert d["goods_eur"] == 239.98 and d["shipping_free"] is True and d["shipping_eur"] == 0.0


async def test_delivery_info_small_article_pays_shipping(mcp, toom):
    d = (await call(mcp, "delivery_info", sku=D, market="Prenzlau"))["delivery"]  # 27.99 < 59
    assert d["goods_eur"] == 27.99 and d["shipping_eur"] == 39.95
    assert d["total_eur"] == pytest.approx(67.94) and d["shipping_free"] is False


async def test_delivery_info_not_deliverable(mcp, toom, monkeypatch):
    def override(request):
        if request.method == "POST" and request.url.path.endswith("/basket/items"):
            return httpx.Response(404, json=[{"type": "error", "message": {"body": "Artikel nicht lieferbar"}}])
        return None

    wrap(toom, monkeypatch, override)
    res = await call(mcp, "delivery_info", sku=A, market="Prenzlau")
    assert res["delivery"]["possible"] is False
    assert "Artikel nicht lieferbar" in res["delivery"]["reason"]
    assert set(res["delivery"]) == {"possible", "reason"}
    assert res["pickup"]["in_stock"] == 1 and res["pickup"]["enough"] is True  # capped at the quantity asked
    assert res["pickup"]["status"] is None and res["pickup"]["hint"] is None and res["pickup"]["note"] is None


async def test_delivery_info_does_not_touch_the_real_basket_session(mcp, toom, monkeypatch):
    seen: list[tuple[str, str, str]] = []

    def override(request):
        seen.append((request.method, request.url.path, request.headers.get("cookie", "")))
        return None

    wrap(toom, monkeypatch, override)
    await call(mcp, "add_to_basket", sku=A, quantity=1, market="Prenzlau")
    await call(mcp, "view_basket", market="Prenzlau")  # the real session now carries toom's session cookie
    real_session = server._sessions[PRENZLAU]
    sessions_before = dict(server._sessions)
    n = len(seen)
    await call(mcp, "delivery_info", sku=DOOR, market="Prenzlau")
    mine = seen[n:]
    basket_calls = [s for s in mine if "/basket" in s[1]]
    assert [(m, p) for m, p, _ in basket_calls] == [("POST", "/shop/rest/V1/toom/basket/items")]
    assert all("PHPSESSID" not in c for _, _, c in basket_calls)  # a throw-away session, not the user's cookie
    assert server._sessions == sessions_before and server._sessions[PRENZLAU] is real_session
    # the real session is still its old self: it still sends its cookie
    n = len(seen)
    await call(mcp, "view_basket", market="Prenzlau")
    assert "PHPSESSID=abc123" in seen[n][2]
    assert not any(m in ("DELETE",) for m, _, _ in seen)  # nothing of the user's basket was removed


async def test_delivery_info_validation(mcp, toom):
    assert "No market selected" in await call_err(mcp, "delivery_info", sku=DOOR)
    assert "quantity must be a whole number" in await call_err(mcp, "delivery_info", sku=DOOR, quantity=0, market="Prenzlau")
    assert "No article with the number 'NOPE'" in await call_err(mcp, "delivery_info", sku="NOPE", market="Prenzlau")


# ============================================================== 7. get_related

async def test_get_related_request_shape(mcp, toom):
    await call(mcp, "get_related", sku=DOOR, market="Prenzlau", limit=3)
    assert len(toom.api_requests) == 1
    path, query, headers = toom.api_requests[0]
    assert path == "/public/api/recommendations/also-bought"
    assert query == {"sku": DOOR, "limit": "3", "market_id": str(PRENZLAU)}
    assert headers["x-client-id"] == "pdp-browser"
    assert headers["host"] == "api.toom.de"


async def test_get_related_also_bought(mcp, toom):
    res = await call(mcp, "get_related", sku=DOOR, market="Prenzlau")
    assert res["kind"] == "also_bought" and res["source"] == "Bondaten" and res["market"] == "Prenzlau"
    assert res["article"] == {"sku": DOOR, "title": "Feuerschutztür 'T30-1 H8-5' 87,5 x 200 cm", "category": "Stahltüren"}
    assert res["count"] == 5 == len(res["items"])
    assert [i["sku"] for i in res["items"]] == ["110023", "110014", "3352130", "2368070", "110011"]
    assert [i["same_category"] for i in res["items"]] == [False, True, False, False, True]
    first = res["items"][0]
    assert first["title"] == "Schrauben inklusive Dübel H8-5 17 Stück" and first["price_eur"] == 15.99
    assert first["was_price_eur"] is None and first["rating"] is None and first["reviews"] == 0
    assert first["category"] == "Werkstatt & Maschinen > Eisenwaren & Beschläge > Dübel"
    assert first["availability"] == {"home_delivery": False, "market_reservable": True, "market_orderable": False}
    assert first["selling_points"][0] == "passend für Hörmann Brandschutztür H8-5 mit Eckzarge" and len(first["selling_points"]) == 5
    assert first["url"] == "https://toom.de/p/schrauben-inklusive-duebel-h8-5-17-stueck/110023"


async def test_get_related_only_other_categories(mcp, toom):
    res = await call(mcp, "get_related", sku=DOOR, market="Prenzlau", only_other_categories=True)
    assert [i["sku"] for i in res["items"]] == ["110023", "3352130", "2368070"]
    assert res["count"] == 3 and all(i["same_category"] is False for i in res["items"])


async def test_get_related_similar(mcp, toom):
    res = await call(mcp, "get_related", sku=DOOR, kind="similar", market="Prenzlau")
    assert toom.api_requests[0][0] == "/public/api/recommendations/similar"
    assert res["kind"] == "similar" and res["source"] is None
    assert [i["sku"] for i in res["items"]] == ["110012", "110014", "110011", "110645", "110644"]
    assert [i["same_category"] for i in res["items"]] == [True, True, True, False, False]
    assert res["items"][3]["rating"] == 5.0 and res["items"][3]["reviews"] == 1
    assert res["items"][4]["availability"] == {"home_delivery": True, "market_reservable": False, "market_orderable": True}


async def test_get_related_field_decoding(mcp, toom):
    v = toom.recommendations["also-bought"]["hits"][0]["values"]
    v.update({"wasPrice": 19.99, "price": 12.5, "Availability": ["0", "1"], "ratingOverallMark": 4.5,
              "ratingTotalReviewsCount": 12, "sellingPoints": "|a|b||c|d|e|f|", "offerName": "Aktion", "offerDate": "bis 31.10."})
    res = await call(mcp, "get_related", sku=DOOR, market="Prenzlau")
    it = res["items"][0]
    assert it["was_price_eur"] == 19.99 and it["price_eur"] == 12.5
    assert it["availability"] == {"home_delivery": True, "market_reservable": True, "market_orderable": False}
    assert it["rating"] == 4.5 and it["reviews"] == 12
    assert it["selling_points"] == ["a", "b", "c", "d", "e"]  # split on "|", empties dropped, at most 5
    assert it["offer"] == "Aktion" and it["offer_period"] == "bis 31.10."
    other = res["items"][1]
    assert other["was_price_eur"] is None  # wasPrice 0 means "no old price"


async def test_get_related_invalid_kind_and_limit(mcp, toom):
    err = await call_err(mcp, "get_related", sku=DOOR, kind="bestsellers", market="Prenzlau")
    assert "kind must be one of ['also_bought', 'similar']" in err
    assert toom.api_requests == []
    for bad in (0, 101, -1):
        assert "limit must be between 1 and 100" in await call_err(mcp, "get_related", sku=DOOR, limit=bad, market="Prenzlau")


async def test_get_related_api_failure_is_a_tool_error(mcp, toom, monkeypatch):
    def override(request):
        if request.url.path.startswith("/public/api/recommendations/"):
            return httpx.Response(503, text="down")
        return None

    wrap(toom, monkeypatch, override)
    assert "HTTP 503" in await call_err(mcp, "get_related", sku=DOOR, market="Prenzlau")


# ====================================================== 8. search best_rated

async def test_search_best_rated_ranking(mcp, toom):
    res = await call(mcp, "search_products", query="akku", market="Prenzlau", sort="best_rated")
    skus = [p["sku"] for p in res["products"]]
    assert set(skus) == {A, B, C, D, E5, F6}
    assert skus[-1] == C and res["products"][-1]["rating_score"] is None  # unrated last
    # same rating 5.0: more reviews rank higher
    assert skus.index(E5) < skus.index(B) < skus.index(D)
    scores = {p["sku"]: p["rating_score"] for p in res["products"]}
    prior = server.RATING_PRIOR  # fixed, not the page mean
    assert scores[A] == pytest.approx((231 * 4.9 + 10 * prior) / 241, abs=1e-3)
    assert scores[F6] == pytest.approx((10 * 4.8 + 10 * prior) / 20, abs=1e-3)
    assert scores[D] == pytest.approx((1 * 5 + 10 * prior) / 11, abs=1e-3)
    assert skus.index(A) < skus.index(D)  # 4.9 from 231 reviews beats 5.0 from one
    ranked = [s for s in skus if scores[s] is not None]
    assert [scores[s] for s in ranked] == sorted((scores[s] for s in ranked), reverse=True)
    assert res["applied"]["sort"] == "best_rated"
    assert toom.search_qs[-1] == ""  # no toom-side sort: the re-ranking is ours


async def test_search_best_rated_keeps_raw_fields(mcp, toom):
    res = await call(mcp, "search_products", query="akku", market="Prenzlau", sort="best_rated")
    p = next(x for x in res["products"] if x["sku"] == A)
    assert p["rating"] == 4.9 and p["reviews"] == 231 and p["price_eur"] == 119.99


async def test_search_other_sorts_have_no_score(mcp, toom):
    res = await call(mcp, "search_products", query="akku", market="Prenzlau")
    assert all("rating_score" not in p for p in res["products"])
    assert [p["sku"] for p in res["products"]][:2] == [A, B]


async def test_search_best_rated_few_reviews_must_not_beat_many_xfail():
    ranked = server._rank_by_rating([
        {"sku": "x1", "rating": 5.0, "reviews": 1},
        {"sku": "x2", "rating": 4.8, "reviews": 200},
    ])
    assert [p["sku"] for p in ranked] == ["x2", "x1"]


def test_rank_by_rating_all_unrated_keeps_order():
    ranked = server._rank_by_rating([{"sku": "a"}, {"sku": "b", "rating": 4.0, "reviews": 0}, {"sku": "c", "rating": None}])
    assert [p["sku"] for p in ranked] == ["a", "b", "c"]
    assert all(p["rating_score"] is None for p in ranked)


def test_rank_by_rating_prefers_more_reviews_at_same_rating():
    ranked = server._rank_by_rating([
        {"sku": "few", "rating": 4.5, "reviews": 2},
        {"sku": "many", "rating": 4.5, "reviews": 80},
        {"sku": "low", "rating": 3.0, "reviews": 80},
    ])
    assert [p["sku"] for p in ranked] == ["many", "few", "low"] or ranked[0]["sku"] == "many"
    assert ranked[0]["rating_score"] > ranked[1]["rating_score"]


async def test_search_min_reviews_drops_and_reports(mcp, toom):
    res = await call(mcp, "search_products", query="akku", market="Prenzlau", min_reviews=5)
    assert [p["sku"] for p in res["products"]] == [A, E5, F6]
    assert res["dropped_for_few_reviews"] == 3 and res["shown"] == 3
    assert "at least 5 reviews" in res["applied"]["filters"]
    assert res["total_hits"] == 488  # toom's count is untouched


async def test_search_min_reviews_one_drops_only_unrated(mcp, toom):
    res = await call(mcp, "search_products", query="akku", market="Prenzlau", min_reviews=1)
    assert C not in [p["sku"] for p in res["products"]] and res["dropped_for_few_reviews"] == 1
    none = await call(mcp, "search_products", query="akku", market="Prenzlau", min_reviews=1000)
    assert none["products"] == [] and none["dropped_for_few_reviews"] == 6 and none["shown"] == 0


async def test_search_min_reviews_zero_drops_nothing(mcp, toom):
    res = await call(mcp, "search_products", query="akku", market="Prenzlau")
    assert res["dropped_for_few_reviews"] == 0 and len(res["products"]) == 6


async def test_search_min_reviews_combined_with_best_rated(mcp, toom):
    res = await call(mcp, "search_products", query="akku", market="Prenzlau", min_reviews=5, sort="best_rated")
    got = res["products"]
    assert {p["sku"] for p in got} == {A, E5, F6}
    assert [p["rating_score"] for p in got] == sorted((p["rating_score"] for p in got), reverse=True)
    assert got[0]["sku"] == A  # 4.9 from 231 reviews outranks 5.0 from 6
    assert res["dropped_for_few_reviews"] == 3


async def test_search_invalid_min_reviews_and_sort(mcp, toom):
    assert "min_reviews must be a whole number >= 0" in await call_err(mcp, "search_products", query="x", market="Prenzlau", min_reviews=-1)
    assert "sort must be one of" in await call_err(mcp, "search_products", query="x", market="Prenzlau", sort="nope")
    assert "best_rated" in await call_err(mcp, "search_products", query="x", market="Prenzlau", sort="nope")


# ================================================================ 9. defaults

def test_env_list_splitting(monkeypatch):
    monkeypatch.setenv("T_LIST", "a; b\n c ;;\n")
    assert server._env_list("T_LIST") == ["a", "b", "c"]
    monkeypatch.setenv("T_LIST", "Mülheim/Ruhr;Berlin-Weißensee")
    assert server._env_list("T_LIST") == ["Mülheim/Ruhr", "Berlin-Weißensee"]
    monkeypatch.setenv("T_LIST", "   ")
    assert server._env_list("T_LIST") == []
    monkeypatch.delenv("T_LIST")
    assert server._env_list("T_LIST") == []


async def test_default_market_needs_no_set_market(mcp, toom, monkeypatch):
    monkeypatch.setenv("TOOMY_DEFAULT_MARKET", "Prenzlau")
    res = await call(mcp, "search_products", query="akku")
    assert res["market"] == "Prenzlau" and toom.queries == [("akku", PRENZLAU)]
    basket = await call(mcp, "view_basket")
    assert basket["market"] == "Prenzlau"
    assert (await call(mcp, "add_to_basket", sku=A))["market"] == "Prenzlau"


async def test_set_market_beats_default_and_explicit_beats_both(mcp, toom, monkeypatch):
    monkeypatch.setenv("TOOMY_DEFAULT_MARKET", "Prenzlau")
    await call(mcp, "set_market", market="Troisdorf")
    assert (await call(mcp, "view_basket"))["market"] == "Troisdorf"
    assert (await call(mcp, "view_basket", market="Köln-Zollstock"))["market"] == "Köln-Zollstock"


async def test_default_market_that_does_not_exist_is_an_error(mcp, toom, monkeypatch):
    monkeypatch.setenv("TOOMY_DEFAULT_MARKET", "Atlantis")
    assert "No toom market found" in await call_err(mcp, "view_basket")


async def test_unset_default_keeps_the_old_error(mcp, toom):
    err = await call_err(mcp, "view_basket")
    assert "No market selected" in err and "set_market" in err
    assert "No market selected" in await call_err(mcp, "search_products", query="x")


async def test_compare_markets_defaults_to_home_markets(mcp, toom, monkeypatch):
    monkeypatch.setenv("TOOMY_HOME_MARKETS", "Friedrichshain;Weißensee")
    toom.set_stock(FRIEDRICHSHAIN, A, 0)
    res = await call(mcp, "compare_markets", items=[{"sku": A, "quantity": 1}])
    assert res["basket_of"] is None and res["items"] == 1
    assert [(r["market"], r["complete"]) for r in res["markets"]] == [("Berlin-Weißensee", True), ("Berlin-Friedrichshain", False)]
    assert res["markets"][1]["short"] == [{"sku": A, "title": A, "wanted": 1, "available": 0}]
    asked = [r[2] for r in toom.requests if r[1] == STOCKS]
    assert sorted(asked) == sorted([FRIEDRICHSHAIN, WEISSENSEE])


async def test_compare_markets_explicit_markets_beat_home_markets(mcp, toom, monkeypatch):
    monkeypatch.setenv("TOOMY_HOME_MARKETS", "Friedrichshain;Weißensee")
    res = await call(mcp, "compare_markets", markets=["Köln-Zollstock"], items=[{"sku": A, "quantity": 1}])
    assert [r["market"] for r in res["markets"]] == ["Köln-Zollstock"]


async def test_compare_markets_without_markets_or_env(mcp, toom):
    assert "TOOMY_HOME_MARKETS" in await call_err(mcp, "compare_markets", items=[{"sku": A, "quantity": 1}])


async def test_plan_pickup_default_primary_and_home_fallbacks(mcp, toom, monkeypatch):
    monkeypatch.setenv("TOOMY_DEFAULT_MARKET", "Prenzlau")
    monkeypatch.setenv("TOOMY_HOME_MARKETS", "Friedrichshain;Weißensee;Prenzlau")
    toom.set_stock(PRENZLAU, B, 0)
    toom.set_stock(FRIEDRICHSHAIN, B, 0)
    toom.set_stock(WEISSENSEE, B, 5)
    res = await call(mcp, "plan_pickup", items=[{"sku": A, "quantity": 1}, {"sku": B, "quantity": 1}])
    assert [(s["market"], s["role"], [p["sku"] for p in s["pick_up"]]) for s in res["stops"]] == [
        ("Prenzlau", "primary", [A]),
        ("Berlin-Weißensee", "fallback", [B]),
    ]
    assert res["complete"] is True and res["all_at_primary_market"] is False and res["missing"] == []
    asked = [r[2] for r in toom.requests if r[1] == STOCKS]
    assert sorted(asked) == sorted([PRENZLAU, FRIEDRICHSHAIN, WEISSENSEE])  # the primary is not asked twice
    assert set(res["stock_matrix"][A]) == {"Prenzlau", "Berlin-Friedrichshain", "Berlin-Weißensee"}


async def test_plan_pickup_uses_selected_market_as_primary(mcp, toom, monkeypatch):
    monkeypatch.setenv("TOOMY_DEFAULT_MARKET", "Prenzlau")
    monkeypatch.setenv("TOOMY_HOME_MARKETS", "Friedrichshain;Köln-Zollstock")
    await call(mcp, "set_market", market="Troisdorf")
    res = await call(mcp, "plan_pickup", items=[{"sku": A, "quantity": 1}])
    assert [(s["market"], s["role"]) for s in res["stops"]] == [("Troisdorf", "primary")]
    assert res["all_at_primary_market"] is True


async def test_plan_pickup_explicit_empty_fallbacks_beat_env(mcp, toom, monkeypatch):
    monkeypatch.setenv("TOOMY_DEFAULT_MARKET", "Prenzlau")
    monkeypatch.setenv("TOOMY_HOME_MARKETS", "Friedrichshain;Weißensee")
    toom.set_stock(PRENZLAU, A, 0)
    res = await call(mcp, "plan_pickup", items=[{"sku": A, "quantity": 1}], fallback_markets=[])
    assert res["complete"] is False and [m["sku"] for m in res["missing"]] == [A]
    assert [r[2] for r in toom.requests if r[1] == STOCKS] == [PRENZLAU]


async def test_plan_pickup_without_any_default_errors(mcp, toom):
    assert "No market selected" in await call_err(mcp, "plan_pickup", items=[{"sku": A, "quantity": 1}])


async def test_home_markets_exclude_primary_and_duplicates(mcp, toom, monkeypatch):
    monkeypatch.setenv("TOOMY_HOME_MARKETS", "Prenzlau;Weißensee;3382;Köln")
    got = await server._home_markets(exclude=market(PRENZLAU))
    assert [m.id for m in got] == [WEISSENSEE, KOELN]
    assert [m.id for m in await server._home_markets()] == [PRENZLAU, WEISSENSEE, KOELN]


async def test_markets_nearby_defaults_to_the_default_market(mcp, toom, monkeypatch):
    monkeypatch.setenv("TOOMY_DEFAULT_MARKET", "Prenzlau")
    res = await call(mcp, "markets_nearby", radius_km=5)
    assert res["centre"]["lat"] == pytest.approx(53.3057, abs=1e-4) and res["centre"]["lon"] == pytest.approx(13.8781, abs=1e-4)
    assert [m["id"] for m in res["markets"]] == [PRENZLAU] and res["markets"][0]["distance_km"] == 0.0
    # and the selected market wins over the env default
    await call(mcp, "set_market", market="Troisdorf")
    res = await call(mcp, "markets_nearby", radius_km=5)
    assert [m["id"] for m in res["markets"]] == [TROISDORF]


async def test_markets_nearby_without_near_or_default_errors(mcp, toom):
    assert "No market selected" in await call_err(mcp, "markets_nearby")


async def test_find_article_nearby_defaults_to_the_default_market(mcp, toom, monkeypatch):
    monkeypatch.setenv("TOOMY_DEFAULT_MARKET", "Prenzlau")
    toom.set_stock(PRENZLAU, A, 0)
    res = await call(mcp, "find_article_nearby", sku=A, radius_km=5)
    assert res["centre"]["lat"] == pytest.approx(53.3057, abs=1e-4)
    assert res["markets_in_radius"] == 1 and res["checked"] == 1
    row = res["markets"][0]
    assert row["id"] == PRENZLAU and row["distance_km"] == 0.0 and row["complete"] is False
    assert row["stock"] == {A: 0} and res["nearest_complete"] is None


async def test_find_article_nearby_explicit_near_beats_default(mcp, toom, monkeypatch):
    monkeypatch.setenv("TOOMY_DEFAULT_MARKET", "Prenzlau")
    res = await call(mcp, "find_article_nearby", near="Troisdorf", sku=A, radius_km=5)
    assert [m["id"] for m in res["markets"]] == [TROISDORF]
    assert res["nearest_complete"]["id"] == TROISDORF
