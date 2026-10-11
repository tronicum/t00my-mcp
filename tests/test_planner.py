"""Pure planning logic: primary market first, fallbacks only for what it lacks (and only if it is not bulky)."""

from __future__ import annotations

import pytest

from toomy.planner import plan_pickup

NAMES = {1: "Fallback-A", 2: "Fallback-B", 3: "Primary"}
LINES = [
    {"sku": "door", "title": "Fire door", "quantity": 2},
    {"sku": "screws", "title": "Screws", "quantity": 3},
    {"sku": "paint", "title": "Paint", "quantity": 1},
]
EVERYTHING = {"door": 9, "screws": 9, "paint": 9}


def plan(stock, bulky=("door",), primary=3, fallback=(1, 2), lines=LINES):
    return plan_pickup(lines, stock, NAMES, list(fallback), primary, set(bulky))


def pickups(result):
    return {s["market"]: {p["sku"]: p["quantity"] for p in s["pick_up"]} for s in result["stops"]}


def test_everything_in_the_primary_market_is_one_stop_even_if_a_fallback_has_everything_too():
    r = plan({1: EVERYTHING, 2: EVERYTHING, 3: EVERYTHING})
    assert pickups(r) == {"Primary": {"door": 2, "screws": 3, "paint": 1}}
    assert r["complete"] and r["all_at_primary_market"]  # no fallback trip, no own transport needed


def test_primary_wins_for_small_items_too_not_only_for_bulky_ones():
    r = plan({1: {"screws": 9}, 3: {"door": 2, "screws": 3, "paint": 1}}, bulky=())
    assert pickups(r) == {"Primary": {"door": 2, "screws": 3, "paint": 1}}


def test_fallback_only_for_what_the_primary_market_lacks():
    stock = {1: {"screws": 3, "paint": 1}, 2: {}, 3: {"door": 2, "paint": 1}}
    r = plan(stock)
    assert pickups(r) == {"Primary": {"door": 2, "paint": 1}, "Fallback-A": {"screws": 3}}
    assert [s["role"] for s in r["stops"]] == ["primary", "fallback"]  # primary stop listed first
    assert not r["all_at_primary_market"]


def test_fallback_stops_are_kept_to_a_minimum():
    stock = {1: {"screws": 3}, 2: {"screws": 3, "paint": 1}, 3: {"door": 2}}
    r = plan(stock)
    assert pickups(r) == {"Primary": {"door": 2}, "Fallback-B": {"screws": 3, "paint": 1}}


def test_bulky_never_comes_from_a_fallback_market_but_the_alternatives_are_reported():
    stock = {1: {"door": 1, "screws": 3, "paint": 1}, 2: {"door": 1}, 3: {"screws": 3, "paint": 1}}
    r = plan(stock)
    assert not r["complete"] and not r["all_at_primary_market"]
    assert pickups(r) == {"Primary": {"screws": 3, "paint": 1}}
    (m,) = r["missing"]
    assert m["sku"] == "door" and m["bulky"] and m["missing"] == 2
    assert m["in_stock_elsewhere"] == {"Fallback-A": 1, "Fallback-B": 1}


def test_a_small_item_that_the_primary_lacks_may_come_from_a_fallback_market():
    stock = {1: {"door": 2}, 3: {"screws": 3, "paint": 1}}
    r = plan(stock, bulky=())  # door is small enough for the car in this variant
    assert pickups(r) == {"Primary": {"screws": 3, "paint": 1}, "Fallback-A": {"door": 2}}
    assert r["complete"]


def test_partial_primary_stock_is_topped_up_from_a_fallback_if_not_bulky():
    stock = {1: {"screws": 2}, 3: {"door": 2, "screws": 1, "paint": 1}}
    r = plan(stock)
    assert pickups(r) == {"Primary": {"door": 2, "screws": 1, "paint": 1}, "Fallback-A": {"screws": 2}}
    assert r["complete"]


def test_partly_available_bulky_is_taken_as_far_as_possible_from_the_primary_only():
    stock = {1: {"door": 5}, 3: {"door": 1, "screws": 3, "paint": 1}}
    r = plan(stock)
    assert pickups(r) == {"Primary": {"door": 1, "screws": 3, "paint": 1}}
    assert r["missing"][0]["missing"] == 1 and r["missing"][0]["in_stock_elsewhere"] == {"Fallback-A": 5}


def test_split_across_fallback_markets_when_none_has_the_full_quantity():
    stock = {1: {"screws": 2}, 2: {"screws": 1}, 3: {"door": 2, "paint": 1}}
    r = plan(stock)
    got = pickups(r)
    assert r["complete"] and got["Fallback-A"]["screws"] + got["Fallback-B"]["screws"] == 3


def test_missing_everywhere_is_flagged_with_no_alternatives():
    r = plan({1: {"screws": 3}, 2: {}, 3: {"door": 2}})
    (m,) = r["missing"]
    assert m["sku"] == "paint" and m["in_stock_elsewhere"] == {} and not m["bulky"]


def test_without_a_primary_market_it_is_plain_fewest_stops():
    stock = {1: {"screws": 3, "paint": 1, "door": 2}, 2: {}}
    r = plan_pickup(LINES, stock, NAMES, [1, 2])
    assert r["complete"] and pickups(r) == {"Fallback-A": {"door": 2, "screws": 3, "paint": 1}}
    assert not r["all_at_primary_market"]


def test_bulky_requires_a_primary_market():
    with pytest.raises(ValueError):
        plan_pickup(LINES, {}, NAMES, [1], None, {"door"})


def test_stock_matrix_is_capped_at_wanted_quantity():
    m = plan({1: {"door": 99}, 3: {"door": 1}})["stock_matrix"]["door"]
    assert m["Fallback-A"] == 2 and m["Primary"] == 1


def test_nothing_available_anywhere_is_not_reported_as_all_at_primary():
    r = plan({1: {}, 3: {}})
    assert not r["complete"] and not r["all_at_primary_market"] and r["stops"] == []
