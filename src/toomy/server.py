"""toomy: MCP server for the toom.de online shop.

Search products, manage a basket per market (toom "Markt"), compare availability across markets and
copy a basket from one market to another. Unofficial; talks to toom.de's own web endpoints.

Scope note: the basket is an anonymous session held by this server process. Checkout/reservation
needs a toom account and happens on toom.de itself; this server does not place orders.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from toomy import planner
from toomy.client import DELIVERY_TYPES, PICKUP, Market, Toom, ToomError, fetch_markets, match_markets

mcp = FastMCP(
    "toomy",
    instructions=(
        "Assistant for the toom.de DIY store. Workflow: find_markets -> set_market -> search_products -> "
        "add_to_basket -> view_basket. Prices and availability depend on the market, so always know which "
        "market you are talking about. To check another store use compare_markets or copy_basket: a market "
        "switch means building a new basket there, and the per-item availability shows what the store has. "
        "This server cannot check out or pay; give the user the basket summary and links."
    ),
)

_transport: httpx.AsyncBaseTransport | None = None  # tests inject a mock transport here
_markets: list[Market] | None = None
_sessions: dict[int, Toom] = {}
_current: Market | None = None


def _fail(e: ToomError) -> ToolError:
    return ToolError(str(e))


async def _all_markets() -> list[Market]:
    global _markets
    if _markets is None:
        try:
            _markets = await fetch_markets(_transport)
        except ToomError as e:
            raise _fail(e) from e
    return _markets


async def _resolve(query: str | int) -> Market:
    markets = await _all_markets()
    q = str(query).strip()
    hits = match_markets(markets, q, limit=10)
    exact = [m for m in hits if str(m.id) == q or m.name.casefold() == q.casefold()]
    if len(exact) == 1:
        return exact[0]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise ToolError(f'No toom market found for "{query}". Try find_markets with a city or postal code.')
    names = ", ".join(f"{m.name} ({m.id})" for m in hits)
    raise ToolError(f'"{query}" is ambiguous: {names}. Use the market id or a more specific name.')


async def _market(market: str | int | None) -> Market:
    if market is not None:
        return await _resolve(market)
    if _current is None:
        raise ToolError("No market selected. Use find_markets, then set_market (or pass market=...).")
    return _current


def _session(m: Market) -> Toom:
    if m.id not in _sessions:
        _sessions[m.id] = Toom(m, transport=_transport)
    return _sessions[m.id]


def _delivery(kind: str) -> str:
    if kind not in DELIVERY_TYPES:
        raise ToolError(f"delivery must be one of {sorted(DELIVERY_TYPES)}")
    return DELIVERY_TYPES[kind]


async def _call(coro):
    try:
        return await coro
    except ToomError as e:
        raise _fail(e) from e


# ------------------------------------------------------------------ markets

@mcp.tool
async def find_markets(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Find toom markets by city, name, street, postal code (prefix) or market id."""
    return [m.public() for m in match_markets(await _all_markets(), query, limit)]


@mcp.tool
async def set_market(market: str) -> dict[str, Any]:
    """Select the market ("Markt") used by default for search and basket. Accepts id, name or city."""
    global _current
    _current = await _resolve(market)
    return _current.public()


# ------------------------------------------------------------------- search

@mcp.tool
async def search_products(query: str, market: str | None = None, limit: int = 20) -> dict[str, Any]:
    """Search toom.de. Returns the first result page only (max ~72 hits, limited by `limit`).

    `availability` decodes toom's code for the selected market: `market_reservable` (can be reserved for
    pickup), `market_orderable` (toom shows "Bestellbar in <Markt>": not in stock; on the product page "Artikel
    anfragen" asks the market to order it, possibly plus transport costs. This server cannot send that
    inquiry: give the user the product `url` so they can do it on toom.de) and `home_delivery`. For real stock quantities
    use compare_markets. Search by words: an exact article number is redirected by toom to a product page.
    """
    m = await _market(market)
    res = await _call(_session(m).search(query))
    return {
        "market": m.name,
        "total_hits": res["count"],
        "shown": min(limit, len(res["products"])),
        "products": res["products"][:limit],
    }


# ------------------------------------------------------------------- basket

@mcp.tool
async def view_basket(market: str | None = None) -> dict[str, Any]:
    """The basket of a market: lines with price and pickup/delivery availability, plus the total."""
    m = await _market(market)
    return {"market": m.name, **await _call(_session(m).basket())}


@mcp.tool
async def add_to_basket(sku: str, quantity: int = 1, delivery: str = "pickup", market: str | None = None) -> dict[str, Any]:
    """Add an article (sku from search_products) to the basket of a market.

    delivery: "pickup" (reserve and collect in the market, default) or "delivery" (parcel to your door).
    Adding an article that is already in the basket increases its quantity.
    """
    if quantity < 1:
        raise ToolError("quantity must be >= 1")
    m = await _market(market)
    return {"market": m.name, **await _call(_session(m).add(sku, quantity, _delivery(delivery)))}


@mcp.tool
async def set_quantity(line_id: int, quantity: int, market: str | None = None) -> dict[str, Any]:
    """Change the quantity of a basket line (line_id from view_basket)."""
    if quantity < 1:
        raise ToolError("quantity must be >= 1; use remove_from_basket to drop a line")
    m = await _market(market)
    return {"market": m.name, **await _call(_session(m).set_quantity(line_id, quantity))}


@mcp.tool
async def remove_from_basket(line_id: int, market: str | None = None) -> dict[str, Any]:
    """Remove one basket line (line_id from view_basket)."""
    m = await _market(market)
    return {"market": m.name, **await _call(_session(m).remove(line_id))}


@mcp.tool
async def clear_basket(market: str | None = None) -> dict[str, Any]:
    """Remove everything from the basket of a market."""
    m = await _market(market)
    return {"market": m.name, **await _call(_session(m).clear())}


SESSION_DIR = Path(os.environ.get("TOOMY_HOME", Path.home() / ".toomy"))


@mcp.tool
async def export_session(market: str | None = None) -> dict[str, Any]:
    """Write the session cookies of a market's basket to a local file, to look at that basket in your own browser.

    The basket lives in an anonymous toom.de session held by this server. Import the file into your browser
    with a cookie manager extension (e.g. Cookie-Editor: Import), then open the basket page. This replaces
    the toom.de cookies of that browser, including a basket it may have had. The file is readable only by you;
    the cookies belong to an anonymous (not logged in) session but are still a session token: do not share them.
    """
    m = await _market(market)
    session = _session(m)
    basket = await _call(session.basket())  # makes sure the server has issued the session cookie
    SESSION_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = SESSION_DIR / f"session-{m.id}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(session.export_cookies(), f, indent=2)
    os.chmod(path, 0o600)
    return {
        "market": m.name,
        "file": str(path),
        "basket_url": "https://toom.de/warenkorb",
        "basket_items": basket["total_quantity"],
        "steps": [
            "Install a cookie manager extension in your browser (e.g. Cookie-Editor).",
            "Open https://toom.de, click the extension, choose Import and paste the content of the file.",
            "Reload and open https://toom.de/warenkorb; you should see this basket.",
        ],
    }


# ---------------------------------------------------- cross-market features

def _report(source: dict[str, Any], target: dict[str, Any]) -> list[dict[str, Any]]:
    """Per source line: is it in the target market's basket, in what quantity, how available, price change."""
    by_key = {(l["sku"], l["delivery_type"]): l for l in target["lines"]}
    out = []
    for s in source["lines"]:
        t = by_key.get((s["sku"], s["delivery_type"]))
        entry: dict[str, Any] = {"sku": s["sku"], "title": s["title"], "wanted": s["quantity"]}
        if t is None:
            entry.update(status="missing", note="not accepted into the basket of the target market")
        else:
            av = t["pickup" if s["delivery_type"] == PICKUP else "delivery"]
            entry.update(
                status="ok" if t["quantity"] == s["quantity"] and av["status"] == "Verfügbar" else "check",
                got=t["quantity"],
                availability=av["text"] or av["status"],
                unit_price=t["unit_price"],
            )
            if s["unit_price_eur"] is not None and t["unit_price_eur"] is not None and s["unit_price_eur"] != t["unit_price_eur"]:
                entry["price_changed_from"] = s["unit_price"]
        out.append(entry)
    return out


async def _copy(src: Market, dst: Market, replace: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the source basket anew in the target market.

    Stock is checked first and only articles the target market has are added (quantity capped at its stock),
    so one unavailable article cannot make the whole copy fail; the report shows what is missing or reduced.
    """
    source = await _call(_session(src).basket())
    if not source["lines"]:
        raise ToolError(f"The basket of {src.name} is empty.")
    target_session = _session(dst)
    stock = await _call(target_session.stock([{"sku": l["sku"], "quantity": l["quantity"]} for l in source["lines"]]))
    if replace:
        await _call(target_session.clear())
    items = [
        {"sku": l["sku"], "quantity": min(l["quantity"], stock.get(l["sku"], 0)), "delivery_type": l["delivery_type"]}
        for l in source["lines"]
        if stock.get(l["sku"], 0) > 0
    ]
    if items:
        await _call(target_session.add_many(items))
    return source, await _call(target_session.basket())


@mcp.tool
async def copy_basket(from_market: str | None = None, to_market: str = "", replace: bool = True) -> dict[str, Any]:
    """Copy the basket of one market into a new basket in another market.

    A market switch means building a new basket there; the per-article report shows whether the target
    market has each article (and in what quantity), plus price changes. The source basket is untouched.
    replace=True (default) empties the target basket first, so repeating the call does not double
    quantities; replace=False adds to what is already there.
    """
    if not to_market:
        raise ToolError("to_market is required")
    src, dst = await _market(from_market), await _resolve(to_market)
    if src.id == dst.id:
        raise ToolError("Source and target market are the same.")
    source, target = await _copy(src, dst, replace)
    return {
        "from": src.name,
        "to": dst.name,
        "report": _report(source, target),
        "total_from": source["total"],
        "total_to": target["total"],
        "basket": target,
    }


async def _lines(src_market: str | None, items: list[dict[str, Any]] | None) -> tuple[Market | None, list[dict[str, Any]]]:
    """Articles to work with: explicit `items` ([{"sku","quantity"}]) or else the basket of a market."""
    if items:
        for i in items:
            if "sku" not in i or int(i.get("quantity", 1)) < 1:
                raise ToolError('each item needs "sku" and quantity >= 1')
        return None, [{"sku": str(i["sku"]), "title": i.get("title"), "quantity": int(i.get("quantity", 1))} for i in items]
    src = await _market(src_market)
    lines = (await _call(_session(src).basket()))["lines"]
    if not lines:
        raise ToolError(f"The basket of {src.name} is empty. Pass items=[{{\"sku\": ..., \"quantity\": ...}}] instead.")
    return src, lines


async def _stock_matrix(lines: list[dict[str, Any]], markets: list[Market]) -> dict[int, dict[str, int]]:
    """One stock request per market for all articles; nothing is added to any basket."""
    items = [{"sku": l["sku"], "quantity": l["quantity"]} for l in lines]
    return {m.id: await _call(_session(m).stock(items)) for m in markets}


@mcp.tool
async def compare_markets(
    markets: list[str], from_market: str | None = None, items: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Check articles against several markets without touching any basket.

    Articles come from `items` ([{"sku": "...", "quantity": 2}]) or, if omitted, from the basket of
    from_market / the current market.

    Uses toom's stock endpoint: per article and market the available quantity (capped at the quantity
    you want). Markets are listed best first (complete before partial).
    """
    src, lines = await _lines(from_market, items)
    targets = [await _resolve(q) for q in markets]
    stock = await _stock_matrix(lines, targets)
    rows = []
    for m in targets:
        short = [
            {"sku": l["sku"], "title": l["title"], "wanted": l["quantity"], "available": stock[m.id].get(l["sku"], 0)}
            for l in lines if stock[m.id].get(l["sku"], 0) < l["quantity"]
        ]
        rows.append({"market": m.name, "complete": not short, "available_items": len(lines) - len(short), "short": short})
    rows.sort(key=lambda r: (not r["complete"], -r["available_items"]))
    return {"basket_of": src.name if src else None, "items": len(lines), "markets": rows}


@mcp.tool
async def plan_pickup(
    primary_market: str,
    fallback_markets: list[str] | None = None,
    bulky_skus: list[str] | None = None,
    from_market: str | None = None,
    items: list[dict[str, Any]] | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    """Plan where to pick up a list of articles (or the current basket): primary market first, fallbacks second.

    Priority: 1) everything the primary market has is picked up there. 2) Only what the primary market does
    not have comes from a fallback market, and only if it is small enough to transport without the special
    vehicle at the primary market. Pass such articles that do NOT fit (doors, long boards, ...) as
    bulky_skus: they are never taken from a fallback market. Decide that from the article's size in its
    title/description. Fallbacks are picked for as few stops as possible. Uses real stock per market.

    `all_at_primary_market` in the result is true when one stop at the primary market covers everything,
    i.e. no trip to a fallback market and no own transport are needed. With apply=True the planned pickups
    are built as (replaced) baskets in each stop's market, ready to be reserved on toom.de.
    """
    _, lines = await _lines(from_market, items)
    primary = await _resolve(primary_market)
    fallbacks = [await _resolve(q) for q in (fallback_markets or [])]
    bulky = {str(b) for b in (bulky_skus or [])}
    unknown = bulky - {l["sku"] for l in lines}
    if unknown:
        raise ToolError(f"bulky_skus not among the articles: {sorted(unknown)}")
    markets = {m.id: m for m in [primary, *fallbacks]}
    stock = await _stock_matrix(lines, list(markets.values()))
    result = planner.plan_pickup(
        [{"sku": l["sku"], "title": l["title"], "quantity": l["quantity"]} for l in lines],
        stock,
        {i: m.name for i, m in markets.items()},
        [m.id for m in fallbacks],
        primary.id,
        bulky,
    )
    if apply:
        for stop in result["stops"]:
            sess = _session(markets[stop["market_id"]])
            await _call(sess.clear())
            await _call(sess.add_many([{"sku": p["sku"], "quantity": p["quantity"], "delivery_type": PICKUP} for p in stop["pick_up"]]))
        result["baskets_built"] = [st["market"] for st in result["stops"]]
    return result


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
