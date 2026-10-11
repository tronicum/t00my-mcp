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
    assert rows["Berlin-Weißensee"]["short"] == [{"sku": A, "title": None, "wanted": 2, "available": 1}]
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
