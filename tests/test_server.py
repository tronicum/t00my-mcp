"""The MCP tools end to end against the fake toom.de."""

from __future__ import annotations

from conftest import call, call_err

A, B, C, D = "1490300", "10458992", "1501938", "1501286"  # 119.99, 149.99, 34.99, 27.99 EUR
FRIEDRICHSHAIN, WEISSENSEE, PRENZLAU = 3646, 3382, 3839


def mutations(toom, market=None):
    """Basket-changing requests the fake saw (compare/plan must never cause any)."""
    return [r for r in toom.requests if "/basket" in r[1] and r[0] != "GET" and (market is None or r[2] == market)]


# ---------------------------------------------------------------- markets

async def test_find_markets(mcp, toom):
    hits = await call(mcp, "find_markets", query="weissensee")
    assert [h["name"] for h in hits] == ["Berlin-Weißensee"]
    assert hits[0]["id"] == WEISSENSEE and "Berlin" in hits[0]["address"]


async def test_set_market_accepts_id_name_or_city(mcp, toom):
    assert (await call(mcp, "set_market", market="3839"))["name"] == "Prenzlau"
    assert (await call(mcp, "set_market", market="prenzlau"))["id"] == PRENZLAU


async def test_ambiguous_or_unknown_market_is_explained(mcp, toom):
    err = await call_err(mcp, "set_market", market="berlin")
    assert "ambiguous" in err and "Berlin-Friedrichshain (3646)" in err and "Berlin-Weißensee (3382)" in err
    assert "No toom market found" in await call_err(mcp, "set_market", market="atlantis")


async def test_market_list_is_loaded_once(mcp, toom):
    await call(mcp, "find_markets", query="berlin")
    await call(mcp, "find_markets", query="prenzlau")
    assert sum(1 for r in toom.requests if r[1] == "/public/api/markets") == 1


# ----------------------------------------------------------------- search

async def test_search_needs_a_market(mcp, toom):
    assert "No market selected" in await call_err(mcp, "search_products", query="akkuschrauber")


async def test_search_uses_the_selected_market_and_limits_results(mcp, toom):
    await call(mcp, "set_market", market="Prenzlau")
    res = await call(mcp, "search_products", query="akku schrauber", limit=2)
    assert res["market"] == "Prenzlau" and res["total_hits"] == 488 and res["shown"] == 2
    assert [p["sku"] for p in res["products"]] == [A, B]
    assert toom.queries == [("akku schrauber", PRENZLAU)]  # the market cookie travelled with the request


async def test_search_with_explicit_market_overrides_the_selected_one(mcp, toom):
    await call(mcp, "set_market", market="Prenzlau")
    await call(mcp, "search_products", query="x", market="Weißensee")
    assert toom.queries[-1][1] == WEISSENSEE


async def test_search_results_are_decoded_for_the_market(mcp, toom):
    await call(mcp, "set_market", market="Prenzlau")
    res = await call(mcp, "search_products", query="akku", limit=1)
    assert res["products"][0]["availability"] == {"home_delivery": True, "market_reservable": True, "market_orderable": False}


async def test_search_by_exact_article_number_explains_toom_redirect(mcp, toom):
    await call(mcp, "set_market", market="Prenzlau")
    assert "search by words" in await call_err(mcp, "search_products", query=A)


async def test_search_reads_only_the_first_page(mcp, toom):
    await call(mcp, "set_market", market="Prenzlau")
    await call(mcp, "search_products", query="akkuschrauber")
    assert [r[1] for r in toom.requests if r[1].startswith("/s/")] == ["/s/akkuschrauber"]  # robots.txt: no ?page=


# ------------------------------------------------- offers, filters, sorting

def last_qs(toom) -> list[str]:
    """Decoded filter/sort parameters of the last search request, as a list of 'key=value'."""
    from urllib.parse import unquote
    return [unquote(p) for p in toom.search_qs[-1].split("&") if p]


async def test_offers_only_sends_toom_offers_filter_and_marks_hits_as_offers(mcp, toom):
    await call(mcp, "set_market", market="Prenzlau")
    res = await call(mcp, "search_products", query="bohrmaschine", offers_only=True)
    assert last_qs(toom) == ["filter=inv(attr.dp_angebot):true"]
    assert res["applied"] == {"filters": ["only offers"], "sort": "relevance"}
    assert all(p["on_offer"] for p in res["products"])  # every hit of the offers filter is an offer


async def test_without_the_offers_filter_on_offer_comes_from_the_hit_itself(mcp, toom):
    await call(mcp, "set_market", market="Prenzlau")
    res = await call(mcp, "search_products", query="akkuschrauber")
    assert toom.search_qs[-1] == ""  # plain search: no params at all
    flags = {p["sku"]: (p["on_offer"], p["promotion"]) for p in res["products"]}
    assert flags[A] == (False, None) and flags[B] == (True, None) and flags[C] == (True, "Gratis Beigabe")


async def test_search_combines_price_brand_availability_and_sort(mcp, toom):
    await call(mcp, "set_market", market="Prenzlau")
    res = await call(
        mcp, "search_products", query="akkuschrauber", brands=["Bosch", "Makita"], min_price=50, max_price=150.5,
        availability="in_market", sort="price_desc", offers_only=True,
    )
    assert last_qs(toom) == [
        "filter=inv(attr.dp_angebot):true",
        "filter=inv(price):[50,150.5]",
        "filter=brands:Bosch~~~Makita",
        f"filter=pickupInStore:{PRENZLAU}",  # availability is filtered per market id
        "sort=inv(price):desc",
    ]
    assert res["applied"]["sort"] == "price_desc"
    assert res["applied"]["filters"] == ["only offers", "price 50–150.5 EUR", "brand Bosch or Makita", "in stock in Prenzlau"]


async def test_one_sided_price_ranges_get_a_default_bound(mcp, toom):
    await call(mcp, "set_market", market="Prenzlau")
    await call(mcp, "search_products", query="x", max_price=100)
    assert "filter=inv(price):[0,100]" in last_qs(toom)
    await call(mcp, "search_products", query="x", min_price=50)
    assert "filter=inv(price):[50,1000000]" in last_qs(toom)  # toom needs both bounds


async def test_every_sort_maps_to_toom_sort_key(mcp, toom):
    await call(mcp, "set_market", market="Prenzlau")
    expected = {"price_asc": "sort=inv(price)", "price_desc": "sort=inv(price):desc", "offers": "sort=inv(attr.dp_angebot)", "rating": "sort=rating:desc"}
    for name, qs in expected.items():
        await call(mcp, "search_products", query="x", sort=name)
        assert last_qs(toom) == [qs]
    await call(mcp, "search_products", query="x", sort="relevance")
    assert toom.search_qs[-1] == ""


async def test_availability_choices_map_to_toom_filter_keys(mcp, toom):
    await call(mcp, "set_market", market="Weißensee")
    for choice, key in (("in_market", "pickupInStore"), ("orderable", "shipToStore"), ("home_delivery", "customFulfillment1")):
        await call(mcp, "search_products", query="x", availability=choice)
        assert last_qs(toom) == [f"filter={key}:{WEISSENSEE}"]


async def test_raw_filters_from_search_filters_can_be_passed_back(mcp, toom):
    await call(mcp, "set_market", market="Prenzlau")
    await call(mcp, "search_products", query="x", filters={"attr.mca_akkuinklusive": ["Ja"], "availability": ["2"]})
    assert last_qs(toom) == ["filter=attr.mca_akkuinklusive:Ja", f"filter=shipToStore:{PRENZLAU}"]


async def test_search_argument_validation(mcp, toom):
    await call(mcp, "set_market", market="Prenzlau")
    assert "sort must be one of" in await call_err(mcp, "search_products", query="x", sort="cheapest")
    assert "availability must be one of" in await call_err(mcp, "search_products", query="x", availability="soon")
    assert "min_price" in await call_err(mcp, "search_products", query="x", min_price=10, max_price=5)
    assert "one availability option" in await call_err(mcp, "search_products", query="x", availability="in_market", filters={"availability": ["0"]})
    assert "set twice" in await call_err(mcp, "search_products", query="x", brands=["Bosch"], filters={"brands": ["Makita"]})
    assert "availability values must be" in await call_err(mcp, "search_products", query="x", filters={"availability": ["9"]})


async def test_filter_arguments_never_put_plus_or_page_in_the_url(mcp, toom):
    await call(mcp, "set_market", market="Prenzlau")
    await call(mcp, "search_products", query="bohr maschine", filters={"attr.mca_einsatzbereich": ["Bohr- und Schraubarbeiten"]}, sort="rating")
    raw = toom.search_qs[-1]
    assert "+" not in raw and "page" not in raw
    assert all("+" not in r[1] for r in toom.requests if r[1].startswith("/s/"))


async def test_search_filters_lists_what_can_be_narrowed_down(mcp, toom):
    await call(mcp, "set_market", market="Prenzlau")
    res = await call(mcp, "search_filters", query="akkuschrauber")
    params = {f["param"]: f for f in res["filters"]}
    assert {"brands", "inv(price)", "inv(attr.dp_angebot)", "attr.mca_akkuinklusive"} <= set(params)
    assert params["inv(price)"]["type"] == "range"
    assert params["brands"]["values"] and {"value", "label", "count"} == set(params["brands"]["values"][0])
    assert res["total_hits"] == 488 and "price_asc" in res["sorts"]
    assert toom.search_qs[-1] == ""  # asking for the filters is a plain search


async def test_searches_do_not_touch_any_basket(mcp, toom):
    await call(mcp, "set_market", market="Prenzlau")
    await call(mcp, "search_products", query="x", offers_only=True, sort="offers")
    await call(mcp, "search_filters", query="x")
    assert mutations(toom) == []


# ----------------------------------------------------------------- basket

async def test_basket_lifecycle(mcp, toom):
    await call(mcp, "set_market", market="Prenzlau")
    b = await call(mcp, "add_to_basket", sku=A, quantity=2)
    assert b["market"] == "Prenzlau" and b["total_eur"] == 239.98
    b = await call(mcp, "add_to_basket", sku=C)
    assert [l["sku"] for l in b["lines"]] == [A, C] and b["total_eur"] == 274.97
    line = b["lines"][1]["line_id"]
    b = await call(mcp, "set_quantity", line_id=line, quantity=3)
    assert b["total_eur"] == 239.98 + 3 * 34.99
    b = await call(mcp, "remove_from_basket", line_id=line)
    assert [l["sku"] for l in b["lines"]] == [A]
    b = await call(mcp, "clear_basket")
    assert b["lines"] == [] and (await call(mcp, "view_basket"))["total_quantity"] == 0


async def test_adding_the_same_article_again_increases_the_quantity(mcp, toom):
    await call(mcp, "set_market", market="Prenzlau")
    await call(mcp, "add_to_basket", sku=A)
    b = await call(mcp, "add_to_basket", sku=A, quantity=2)
    assert [(l["sku"], l["quantity"]) for l in b["lines"]] == [(A, 3)]


async def test_delivery_type_is_sent_as_toom_expects(mcp, toom):
    await call(mcp, "set_market", market="Prenzlau")
    b = await call(mcp, "add_to_basket", sku=A, delivery="delivery")
    assert b["lines"][0]["delivery_type"] == "homeDelivery_homeDelivery"
    assert "delivery must be one of" in await call_err(mcp, "add_to_basket", sku=A, delivery="drone")


async def test_article_not_in_stock_cannot_be_added_and_toom_message_is_shown(mcp, toom):
    toom.set_stock(PRENZLAU, A, 0)
    await call(mcp, "set_market", market="Prenzlau")
    assert "not available" in await call_err(mcp, "add_to_basket", sku=A)


async def test_quantity_validation(mcp, toom):
    await call(mcp, "set_market", market="Prenzlau")
    assert "quantity" in await call_err(mcp, "add_to_basket", sku=A, quantity=0)
    assert "quantity" in await call_err(mcp, "set_quantity", line_id=1, quantity=0)


async def test_every_market_has_its_own_basket(mcp, toom):
    await call(mcp, "add_to_basket", sku=A, market="Prenzlau")
    await call(mcp, "add_to_basket", sku=C, market="Weißensee")
    assert [l["sku"] for l in (await call(mcp, "view_basket", market="Prenzlau"))["lines"]] == [A]
    assert [l["sku"] for l in (await call(mcp, "view_basket", market="Weißensee"))["lines"]] == [C]


# ---------------------------------------------------------------- session

async def test_export_session_asks_for_confirmation_and_writes_nothing_without_it(mcp, toom, tmp_path, monkeypatch):
    from t00my_mcp import server

    home = tmp_path / "t00my-home"
    monkeypatch.setattr(server, "SESSION_DIR", home)
    res = await call(mcp, "export_session", market="Prenzlau")
    assert res["written"] is False and "replaces" in res["warning"] and "confirm=True" in res["next"]
    assert res["would_write"] == str(home / f"session-{PRENZLAU}.json")
    assert not home.exists()  # nothing written, no directory created
    assert [r for r in toom.requests if "/basket" in r[1]] == []  # not even a request to toom


async def test_export_session_writes_importable_cookies_with_private_permissions(mcp, toom, tmp_path, monkeypatch):
    import json
    import stat
    from t00my_mcp import server

    monkeypatch.setattr(server, "SESSION_DIR", tmp_path / "t00my-home")
    await call(mcp, "add_to_basket", sku=A, market="Prenzlau")
    res = await call(mcp, "export_session", market="Prenzlau", confirm=True)
    path = tmp_path / "t00my-home" / f"session-{PRENZLAU}.json"
    assert res["written"] and res["file"] == str(path) and res["basket_items"] == 1 and res["basket_url"].endswith("/warenkorb")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600  # file readable only by the owner
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    cookies = {c["name"]: c for c in json.loads(path.read_text())}
    assert cookies["market_id"]["value"] == str(PRENZLAU)  # the market travels with the session
    assert cookies["market_name"]["value"] == "Prenzlau"
    assert cookies["PHPSESSID"]["value"] == "abc123" and cookies["PHPSESSID"]["httpOnly"] and cookies["PHPSESSID"]["secure"]
    assert not cookies["market_id"]["httpOnly"] and not cookies["market_name"]["httpOnly"]  # the site's JS reads them
    assert all(c["domain"].endswith("toom.de") and c["path"] == "/" for c in cookies.values())


async def test_export_session_needs_a_market(mcp, toom):
    assert "No market selected" in await call_err(mcp, "export_session")


# ---------------------------------------------------------- article request

async def test_prepare_request_for_an_article_not_in_stock(mcp, toom):
    toom.set_stock(PRENZLAU, A, 0)
    res = await call(
        mcp, "prepare_article_request", sku=A, market="Prenzlau", quantity=2, needed_by="Freitag, 16.10.", note="Gern mit Rückruf."
    )
    assert res["status"] == "not_in_stock" and res["stock_in_market"] == 0 and res["quantity_wanted"] == 2
    assert res["article"]["sku"] == A and res["article"]["url"].endswith(f"/{A}")
    assert "Akku-Bohrschrauber" in res["article"]["title"] and "&#039;" not in res["article"]["title"]  # entities decoded
    assert res["market"]["name"] == "Prenzlau" and res["market"]["phone"]
    text = res["draft_text"]
    assert f"2× {res['article']['title']} (Artikelnummer {A})" in text and "Prenzlau" in text
    assert "Freitag, 16.10." in text and "Gern mit Rückruf." in text and "Transportkosten" in text
    assert any("Artikel anfragen" in s for s in res["steps"]) and any(res["market"]["phone"] in s for s in res["steps"])
    assert "Nothing was sent" in res["note"]
    # reads only: no basket change, nothing POSTed except the stock check
    assert mutations(toom) == [] and [r for r in toom.requests if r[0] == "POST" and "stocks" not in r[1]] == []


async def test_prepare_request_reports_partial_and_full_stock(mcp, toom):
    toom.set_stock(PRENZLAU, A, 1)
    assert (await call(mcp, "prepare_article_request", sku=A, market="Prenzlau", quantity=2))["status"] == "partly_in_stock"
    full = await call(mcp, "prepare_article_request", sku=A, market="Prenzlau", quantity=1)
    assert full["status"] == "in_stock" and "no inquiry needed" in full["steps"][0]


async def test_prepare_request_uses_the_selected_market_and_validates(mcp, toom):
    assert "No market selected" in await call_err(mcp, "prepare_article_request", sku=A)
    await call(mcp, "set_market", market="Weißensee")
    assert (await call(mcp, "prepare_article_request", sku=A))["market"]["name"] == "Berlin-Weißensee"
    assert "quantity" in await call_err(mcp, "prepare_article_request", sku=A, quantity=0)
    assert "No article with the number" in await call_err(mcp, "prepare_article_request", sku="999999999")


async def test_prepare_request_contains_no_personal_data(mcp, toom):
    res = await call(mcp, "prepare_article_request", sku=A, market="Prenzlau")
    flat = str(res).lower()
    assert "@" not in flat.replace("@example.invalid", "")  # no e-mail address of the user, only the (placeholder) market contact
    assert "name:" not in flat and "adresse des" not in flat


# ------------------------------------------------------------ copy basket

async def test_copy_basket_builds_a_new_basket_and_reports_availability(mcp, toom):
    await call(mcp, "add_to_basket", sku=A, quantity=2, market="Friedrichshain")
    await call(mcp, "add_to_basket", sku=C, market="Friedrichshain")
    toom.set_stock(PRENZLAU, C, 0)  # missing in the target market
    toom.set_stock(PRENZLAU, A, 1)  # only one of two

    res = await call(mcp, "copy_basket", from_market="Friedrichshain", to_market="Prenzlau")
    by_sku = {r["sku"]: r for r in res["report"]}
    assert res["from"] == "Berlin-Friedrichshain" and res["to"] == "Prenzlau"
    assert by_sku[C]["status"] == "missing"
    assert by_sku[A]["status"] == "check"  # in the basket but not fully available there
    assert by_sku[A]["wanted"] == 2 and by_sku[A]["got"] == 1  # capped at the target market's stock
    assert [(l["sku"], l["quantity"]) for l in res["basket"]["lines"]] == [(A, 1)]  # C was not added at all
    # the source basket is untouched
    src = await call(mcp, "view_basket", market="Friedrichshain")
    assert [(l["sku"], l["quantity"]) for l in src["lines"]] == [(A, 2), (C, 1)]


async def test_copy_basket_fully_available_is_ok_and_repeatable(mcp, toom):
    await call(mcp, "add_to_basket", sku=A, quantity=2, market="Friedrichshain")
    first = await call(mcp, "copy_basket", from_market="Friedrichshain", to_market="Prenzlau")
    again = await call(mcp, "copy_basket", from_market="Friedrichshain", to_market="Prenzlau")
    assert [r["status"] for r in first["report"]] == ["ok"]
    assert again["basket"]["total_quantity"] == 2  # replace=True: no doubling
    kept = await call(mcp, "copy_basket", from_market="Friedrichshain", to_market="Prenzlau", replace=False)
    assert kept["basket"]["total_quantity"] == 4  # replace=False adds on top


async def test_copy_basket_edge_cases(mcp, toom):
    await call(mcp, "set_market", market="Friedrichshain")
    assert "empty" in await call_err(mcp, "copy_basket", to_market="Prenzlau")
    await call(mcp, "add_to_basket", sku=A)
    assert "same" in await call_err(mcp, "copy_basket", to_market="Friedrichshain")
    assert "to_market is required" in await call_err(mcp, "copy_basket")


# --------------------------------------------------- compare / plan (stock)

async def test_compare_markets_from_items_touches_no_basket(mcp, toom):
    toom.set_stock(WEISSENSEE, A, 1)
    toom.set_stock(PRENZLAU, C, 0)
    items = [{"sku": A, "quantity": 2}, {"sku": C, "quantity": 1}]
    res = await call(mcp, "compare_markets", markets=["Friedrichshain", "Weißensee", "Prenzlau"], items=items)
    rows = {r["market"]: r for r in res["markets"]}
    assert rows["Berlin-Friedrichshain"]["complete"]
    assert rows["Berlin-Weißensee"]["short"] == [{"sku": A, "title": A, "wanted": 2, "available": 1}]
    assert rows["Prenzlau"]["short"][0]["sku"] == C
    assert res["markets"][0]["market"] == "Berlin-Friedrichshain"  # complete markets first
    assert mutations(toom) == []


async def test_compare_markets_from_the_basket(mcp, toom):
    await call(mcp, "add_to_basket", sku=A, quantity=2, market="Friedrichshain")
    toom.set_stock(PRENZLAU, A, 1)
    res = await call(mcp, "compare_markets", markets=["Prenzlau"], from_market="Friedrichshain")
    assert res["basket_of"] == "Berlin-Friedrichshain" and not res["markets"][0]["complete"]


async def test_compare_needs_articles(mcp, toom):
    await call(mcp, "set_market", market="Friedrichshain")
    assert "empty" in await call_err(mcp, "compare_markets", markets=["Prenzlau"])
    assert "sku" in await call_err(mcp, "compare_markets", markets=["Prenzlau"], items=[{"quantity": 1}])


async def test_plan_pickup_primary_first_fallback_only_for_what_is_missing(mcp, toom):
    """Everything the primary market has is picked up there; a small item it lacks comes from a fallback."""
    toom.set_stock(PRENZLAU, C, 0)  # small item missing at the primary market
    toom.set_stock(FRIEDRICHSHAIN, B, 0)
    items = [{"sku": A, "quantity": 1, "title": "bulky"}, {"sku": B, "quantity": 1}, {"sku": C, "quantity": 2}]
    res = await call(
        mcp, "plan_pickup", primary_market="Prenzlau", fallback_markets=["Friedrichshain", "Weißensee"], bulky_skus=[A], items=items
    )
    assert res["complete"] and not res["all_at_primary_market"]
    got = {s["market"]: {p["sku"]: p["quantity"] for p in s["pick_up"]} for s in res["stops"]}
    assert got == {"Prenzlau": {A: 1, B: 1}, "Berlin-Friedrichshain": {C: 2}}
    assert [s["role"] for s in res["stops"]] == ["primary", "fallback"]
    assert mutations(toom) == []  # planning only reads


async def test_plan_pickup_all_at_primary_means_no_fallback_trip(mcp, toom):
    items = [{"sku": A, "quantity": 1}, {"sku": C, "quantity": 2}]
    res = await call(mcp, "plan_pickup", primary_market="Prenzlau", fallback_markets=["Friedrichshain"], bulky_skus=[A], items=items)
    assert res["all_at_primary_market"] and [s["market"] for s in res["stops"]] == ["Prenzlau"]


async def test_plan_pickup_bulky_missing_at_the_primary_is_not_moved_to_a_fallback(mcp, toom):
    toom.set_stock(PRENZLAU, A, 0)
    res = await call(
        mcp, "plan_pickup", primary_market="Prenzlau", fallback_markets=["Friedrichshain"], bulky_skus=[A], items=[{"sku": A, "quantity": 1}]
    )
    assert not res["complete"] and res["stops"] == []
    assert res["missing"][0]["in_stock_elsewhere"] == {"Berlin-Friedrichshain": 1}  # capped at the wanted quantity


async def test_plan_pickup_works_without_fallback_markets(mcp, toom):
    toom.set_stock(PRENZLAU, C, 0)
    res = await call(mcp, "plan_pickup", primary_market="Prenzlau", items=[{"sku": A, "quantity": 1}, {"sku": C, "quantity": 1}])
    assert [m["sku"] for m in res["missing"]] == [C] and res["stops"][0]["market"] == "Prenzlau"


async def test_plan_pickup_apply_builds_baskets_only_where_there_is_a_stop(mcp, toom):
    await call(mcp, "add_to_basket", sku=D, market="Weißensee")  # stale content in an unused market stays untouched
    await call(mcp, "add_to_basket", sku=D, market="Prenzlau")  # stale content at a stop must be replaced
    items = [{"sku": A, "quantity": 1}, {"sku": C, "quantity": 2}]
    res = await call(
        mcp, "plan_pickup", primary_market="Prenzlau", fallback_markets=["Weißensee", "Friedrichshain"], bulky_skus=[A], items=items, apply=True
    )
    assert res["baskets_built"] == ["Prenzlau"]  # the primary market has everything: a single stop
    pr = await call(mcp, "view_basket", market="Prenzlau")
    assert [(l["sku"], l["quantity"]) for l in pr["lines"]] == [(A, 1), (C, 2)]
    assert (await call(mcp, "view_basket", market="Weißensee"))["lines"][0]["sku"] == D


async def test_plan_pickup_validation(mcp, toom):
    items = [{"sku": A, "quantity": 1}]
    assert "not among the articles" in await call_err(mcp, "plan_pickup", primary_market="Prenzlau", bulky_skus=["nope"], items=items)
    assert "No toom market found" in await call_err(mcp, "plan_pickup", primary_market="Atlantis", items=items)


async def test_plan_pickup_from_the_basket(mcp, toom):
    await call(mcp, "add_to_basket", sku=A, market="Friedrichshain")
    toom.set_stock(PRENZLAU, A, 0)
    res = await call(mcp, "plan_pickup", primary_market="Prenzlau", fallback_markets=["Weißensee"], from_market="Friedrichshain")
    assert res["complete"] and res["stops"][0]["market"] == "Berlin-Weißensee"


# ------------------------------------------------------------- failures

async def test_toom_errors_become_tool_errors(mcp, toom):
    await call(mcp, "set_market", market="Prenzlau")
    toom.catalog.pop(A)
    assert "toom.de" in await call_err(mcp, "add_to_basket", sku=A)
