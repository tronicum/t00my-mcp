"""Contract tests against the REAL toom.de. Opt-in: TOOMY_LIVE=1 uv run pytest -m live

They use an anonymous throwaway session (no login, nothing is ordered) and a handful of requests, and
check that the shapes the mocked tests rely on are still what toom.de sends.
"""

from __future__ import annotations

import os

import pytest

from toomy import client
from toomy.client import PICKUP, Toom, fetch_markets, match_markets

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("TOOMY_LIVE"), reason="set TOOMY_LIVE=1 to talk to the real toom.de"),
]


@pytest.fixture(scope="module")
async def markets():
    return await fetch_markets()


@pytest.fixture
async def session(markets):
    s = Toom(match_markets(markets, "Prenzlau")[0])
    yield s
    await s.aclose()


async def test_market_list(markets):
    assert len(markets) > 100
    m = match_markets(markets, "Prenzlau")[0]
    assert m.id == 3839 and m.zip == "17291"


async def test_search_page_still_embeds_the_product_list(session):
    res = await session.search("akkuschrauber")
    assert res["count"] > 0 and res["products"]
    p = res["products"][0]
    assert p["sku"] and p["title"] and p["price_eur"] and p["url"].startswith("https://toom.de/p/")


async def test_offers_filter_sorting_and_price_range_work_on_the_search_page(session):
    from toomy.client import FILTER_OFFERS, FILTER_PRICE, SORTS

    plain = await session.search("bohrmaschine")
    offers = await session.search("bohrmaschine", {FILTER_OFFERS: ["true"]})
    assert 0 < offers["count"] < plain["count"]
    cheap = await session.search("bohrmaschine", {FILTER_PRICE: ["[0,50]"]}, SORTS["price_desc"])
    prices = [p["price_eur"] for p in cheap["products"] if p["price_eur"] is not None]
    assert prices and max(prices) <= 50 and prices == sorted(prices, reverse=True)
    brands = next(f for f in plain["filters"] if f["param"] == "brands")
    assert brands["type"] == "choice" and brands["values"][0]["count"] > 0


async def test_availability_filter_is_per_market(session):
    from toomy.client import AVAILABILITY_FILTERS

    plain = await session.search("bohrmaschine")
    in_market = await session.search("bohrmaschine", {AVAILABILITY_FILTERS["1"]: [str(session.market.id)]})
    assert 0 < in_market["count"] < plain["count"]
    assert all(p["availability"]["market_reservable"] for p in in_market["products"])


async def test_stock_endpoint_answers_per_article(session):
    sku = (await session.search("akkuschrauber"))["products"][0]["sku"]
    stock = await session.stock([{"sku": sku, "quantity": 1}])
    assert set(stock) == {sku} and isinstance(stock[sku], int)


async def test_basket_roundtrip_in_an_anonymous_session(session):
    sku = (await session.search("akkuschrauber"))["products"][0]["sku"]
    assert (await session.basket())["lines"] == []
    b = await session.add_many([{"sku": sku, "quantity": 1, "delivery_type": PICKUP}])  # list body, as the site sends
    (line,) = b["lines"]
    assert line["sku"] == sku and line["delivery_type"] == PICKUP and line["pickup"]["status"]
    b = await session.set_quantity(line["line_id"], 2)
    assert b["lines"][0]["quantity"] == 2 and b["total_eur"]
    assert (await session.clear())["lines"] == []


async def test_search_is_throttled_and_polite():
    assert client.MIN_INTERVAL >= 0.5 and "toomy" in client.USER_AGENT
