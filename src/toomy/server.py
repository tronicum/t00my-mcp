"""toomy: MCP server for the toom.de online shop.

Search products, manage a basket per market (toom "Markt"), compare availability across markets and
copy a basket from one market to another. Unofficial; talks to toom.de's own web endpoints.

Scope note: the basket is an anonymous session held by this server process. Checkout/reservation
needs a toom account and happens on toom.de itself; this server does not place orders.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from toomy import geo, planner
from toomy.client import (
    AVAILABILITY_FILTERS,
    DELIVERY_TYPES,
    FILTER_OFFERS,
    FILTER_PRICE,
    MAX_PRICE,
    PICKUP,
    SORTS,
    Market,
    Toom,
    ToomError,
    fetch_markets,
    match_markets,
)

mcp = FastMCP(
    "toomy",
    instructions=(
        "Assistant for the toom.de DIY store. Workflow: find_markets -> set_market -> search_products -> "
        "add_to_basket -> view_basket. Prices and availability depend on the market, so always know which "
        "market you are talking about. To check another store use compare_markets or copy_basket: a market "
        "switch means building a new basket there, and the per-item availability shows what the store has. "
        "For \"where can I get X near <place>\" use find_article_nearby (straight-line radius, stock per market); "
        "get_article shows an article's details. "
        "This server cannot check out, pay or send inquiries; give the user the basket summary and links. "
        "export_session replaces the toom.de cookies in the user's browser: explain that and get the "
        "user's explicit OK before calling it with confirm=True."
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

AVAILABILITY_CHOICES = {"in_market": "1", "orderable": "2", "home_delivery": "0"}


def _num(x: float) -> str:
    """50 -> '50', 150.5 -> '150.5', 1000000 -> '1000000' (never an exponent: it would put a '+' in the URL)."""
    return f"{x:.2f}".rstrip("0").rstrip(".")


def _search_filters(
    market: Market,
    offers_only: bool,
    min_price: float | None,
    max_price: float | None,
    brands: list[str] | None,
    availability: str | None,
    filters: dict[str, list[str]] | None,
) -> tuple[dict[str, list[str]], list[str]]:
    """Translate the tool arguments into toom filter params, plus a human-readable summary."""
    out: dict[str, list[str]] = {}
    summary: list[str] = []
    if offers_only:
        out[FILTER_OFFERS] = ["true"]
        summary.append("only offers")
    if min_price is not None or max_price is not None:
        lo, hi = (min_price or 0), (max_price if max_price is not None else MAX_PRICE)
        if lo > hi:
            raise ToolError("min_price must not be greater than max_price")
        out[FILTER_PRICE] = [f"[{_num(lo)},{_num(hi)}]"]
        summary.append(f"price {_num(lo)}–{'∞' if max_price is None else _num(hi)} EUR")
    if brands:
        out["brands"] = list(brands)
        summary.append("brand " + " or ".join(brands))
    avail_codes: list[str] = []
    if availability:
        if availability not in AVAILABILITY_CHOICES:
            raise ToolError(f"availability must be one of {sorted(AVAILABILITY_CHOICES)}")
        avail_codes.append(AVAILABILITY_CHOICES[availability])
    for param, values in (filters or {}).items():
        if param == "availability":  # as listed by search_filters: "0"/"1"/"2"
            bad = [v for v in values if v not in AVAILABILITY_FILTERS]
            if bad:
                raise ToolError(f"availability values must be one of {sorted(AVAILABILITY_FILTERS)}, got {bad}")
            avail_codes.extend(values)
        elif param in AVAILABILITY_FILTERS.values():
            raise ToolError(f'use availability=... (or the "availability" filter) instead of "{param}"')
        elif param in out:
            raise ToolError(f'"{param}" is set twice (by a named argument and in filters)')
        else:
            out[param] = [str(v) for v in values]
            summary.append(f"{param}={' or '.join(map(str, values))}")
    if len(set(avail_codes)) > 1:
        raise ToolError("toom can filter by one availability option at a time (in_market, orderable or home_delivery)")
    if avail_codes:
        code = avail_codes[0]
        out[AVAILABILITY_FILTERS[code]] = [str(market.id)]
        summary.append({"1": f"in stock in {market.name}", "2": f"orderable in {market.name}", "0": "home delivery"}[code])
    return out, summary


@mcp.tool
async def search_products(
    query: str,
    market: str | None = None,
    limit: int = 20,
    offers_only: bool = False,
    sort: str = "relevance",
    min_price: float | None = None,
    max_price: float | None = None,
    brands: list[str] | None = None,
    availability: str | None = None,
    filters: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Search toom.de, optionally narrowed down. First result page only (max ~72 hits, shown up to `limit`).

    Examples: "a drill that is on offer" -> query="bohrmaschine", offers_only=True. "cheapest cordless
    screwdriver in stock here" -> query="akkuschrauber", availability="in_market", sort="price_asc".
    "Bosch or Makita under 150 EUR" -> brands=["Bosch","Makita"], max_price=150.

    offers_only: toom's "Nur Angebote anzeigen". sort: relevance | price_asc | price_desc | offers | rating.
    availability: in_market (in stock in the selected market) | orderable (not in stock, the market can order
    it) | home_delivery. `filters` takes toom's own filter params and values as listed by search_filters
    (e.g. {"attr.mca_akkuinklusive": ["Ja"]}); different filters are combined with AND, values of one filter
    with OR.

    Each hit has `on_offer` (toom's offer flag or a promotion such as "Gratis Beigabe", see `promotion`) and
    `availability` for the market: `market_reservable`, `market_orderable` ("Auf Anfrage bestellbar": the
    inquiry is done by the user on the product page) and `home_delivery`. For stock quantities use
    compare_markets. Search by words: an exact article number is redirected by toom to a product page.
    """
    if sort not in SORTS:
        raise ToolError(f"sort must be one of {sorted(SORTS)}")
    if not query.strip():
        raise ToolError("query must not be empty")
    _check_limit(limit)
    for name, val in (("min_price", min_price), ("max_price", max_price)):
        if val is not None and (not math.isfinite(val) or val < 0):
            raise ToolError(f"{name} must be a number >= 0")
    m = await _market(market)
    toom_filters, summary = _search_filters(m, offers_only, min_price, max_price, brands, availability, filters)
    res = await _call(_session(m).search(query, toom_filters, SORTS[sort]))
    products = res["products"]
    if offers_only:  # every hit of toom's offers filter is an offer, even without an offer flag in the card
        products = [{**p, "on_offer": True} for p in products]
    return {
        "market": m.name,
        "total_hits": res["count"],
        "shown": min(limit, len(products)),
        "applied": {"filters": summary, "sort": sort},
        "products": products[:limit],
    }


@mcp.tool
async def search_filters(query: str, market: str | None = None) -> dict[str, Any]:
    """Which filters toom offers for a search term, with the number of hits per value.

    Use it to narrow a broad search down (brand, cordless or corded, battery included, ...). The result lists
    each filter's `param` and its values; pass them on in search_products(filters={param: [value, ...]}).
    Range filters (price, voltage, weight, ...) show their min and max; price has its own arguments
    min_price/max_price. The category tree is not included.
    """
    if not query.strip():
        raise ToolError("query must not be empty")
    m = await _market(market)
    res = await _call(_session(m).search(query))
    return {"market": m.name, "total_hits": res["count"], "filters": res["filters"], "sorts": sorted(SORTS)}


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
async def export_session(market: str | None = None, confirm: bool = False) -> dict[str, Any]:
    """Write the session cookies of a market's basket to a local file, to look at that basket in your own browser.

    The basket lives in an anonymous toom.de session held by this server. Importing the file into your
    browser (cookie manager extension, e.g. Cookie-Editor: Import) REPLACES the toom.de cookies of that
    browser, including any basket or market choice it had there. The cookies are an anonymous session
    token (no login), but still a token: keep the file private and do not share it.

    Ask the user first. Without confirm=True nothing is written and you get the notice to show them; call
    again with confirm=True only after the user explicitly agreed.
    """
    m = await _market(market)
    path = SESSION_DIR / f"session-{m.id}.json"
    if not confirm:
        return {
            "written": False,
            "market": m.name,
            "would_write": str(path),
            "warning": (
                "Importing this file into your browser replaces its toom.de cookies, including a basket or "
                "market choice it has there. The cookies are an anonymous session token (no login) but still a "
                "token: do not share the file."
            ),
            "next": "Ask the user whether they want this; if yes, call export_session again with confirm=True.",
        }
    session = _session(m)
    basket = await _call(session.basket())  # makes sure the server has issued the session cookie
    SESSION_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(session.export_cookies(), f, indent=2)
    os.chmod(path, 0o600)
    return {
        "written": True,
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


@mcp.tool
async def prepare_article_request(
    sku: str,
    market: str | None = None,
    quantity: int = 1,
    needed_by: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Prepare asking a market to order an article that is not in stock there. Sends nothing.

    toom shows "Auf Anfrage bestellbar" / "Artikel anfragen" on the product page for such articles and the
    market orders them for you (possibly plus transport costs). The request form is protected by a captcha
    and carries your contact data, so it is done by the user on toom.de. This tool gathers everything
    needed: the stock in the market, the product page link, the market's phone number and a ready-made
    text for the form or a phone call. `needed_by` (free text, e.g. "Freitag, 16.10.") and `note` are
    added to that text. No personal data is read or sent.
    """
    if quantity < 1:
        raise ToolError("quantity must be >= 1")
    m = await _market(market)
    session = _session(m)
    product = await _call(session.product(sku))
    stock = (await _call(session.stock([{"sku": product["sku"], "quantity": quantity}]))).get(product["sku"], 0)
    availability = await _availability(session, product["sku"], product["title"])
    if stock >= quantity:
        status = "in_stock"
    elif stock > 0:
        status = "partly_in_stock"
    else:
        status = "not_in_stock"

    wanted = f"{quantity}× {product['title']} (Artikelnummer {product['sku']})"
    lines = [
        "Guten Tag,",
        f"ich möchte gern {wanted} im toom Markt {m.name} bestellen und dort abholen.",
    ]
    if needed_by:
        lines.append(f"Ich benötige den Artikel bis: {needed_by}.")
    lines.append("Bitte um Rückmeldung, bis wann der Artikel da sein kann und ob Transportkosten anfallen.")
    if note:
        lines.append(note)
    lines.append("Vielen Dank!")

    if status == "in_stock":
        steps = [f"It is in stock in {m.name} ({stock}): no inquiry needed, add it to the basket and reserve it on toom.de."]
    else:
        steps = [
            f"Open the product page: {product['url']}",
            f"Select the market {m.name} (\"Mein Markt\") if the page shows another one.",
            'Click "Artikel anfragen" (shown as "Auf Anfrage bestellbar"), fill in your contact data and solve the captcha.',
            f"Or call the market: {m.phone}" if m.phone else "Or call the market.",
        ]
    return {
        "article": product,
        "market": m.public(),
        "quantity_wanted": quantity,
        "stock_in_market": stock,
        "status": status,
        "availability": availability,
        "request_possible_online": bool(availability and availability["market_orderable"]) if availability else None,
        "steps": steps,
        "draft_text": "\n".join(lines),
        "note": "Nothing was sent. The request form needs your contact data and a captcha; do it on toom.de.",
    }


# ------------------------------------------------------------ article details

@mcp.tool
async def get_article(
    sku: str,
    market: str | None = None,
    quantity: int = 1,
    also_check: list[str] | None = None,
) -> dict[str, Any]:
    """Details of one article by its toom article number, plus stock in the market(s).

    Gives title, brand, price, EAN, selling points, characteristics (dimensions, technical details, scope of
    delivery, ...), categories, images, documents and manufacturer contact, all as toom's product page
    shows them. `stock` is the available quantity (capped at `quantity`) in the selected market and in each
    market of `also_check`; `availability` decodes toom's code for the selected market ("Auf Anfrage
    bestellbar" = `market_orderable`). Nothing is added to a basket.
    """
    quantity = _int_quantity(quantity)
    if len(also_check or []) > MAX_ALSO_CHECK:
        raise ToolError(f"also_check takes at most {MAX_ALSO_CHECK} markets")
    m = await _market(market)
    others = [await _resolve(q) for q in (also_check or [])]  # resolved before any article request is made
    session = _session(m)
    art = await _call(session.article(sku))
    targets = [m] + [x for x in others if x.id != m.id]
    stock: dict[str, int | str] = {}
    for t in targets:
        try:
            got = await _session(t).stock([{"sku": art["sku"], "quantity": quantity}])
            stock[t.name] = got.get(art["sku"], 0)
        except ToomError as e:
            stock[t.name] = f"unknown ({e})"
    return {
        "article": art,
        "market": m.name,
        "quantity_asked": quantity,
        "stock": stock,
        "availability": await _availability(session, art["sku"], art["title"]),
    }


# ----------------------------------------------------------------- nearby

@mcp.tool
async def markets_nearby(near: str, radius_km: float = 50, limit: int = 30) -> dict[str, Any]:
    """toom markets within a radius (straight-line distance, nearest first).

    `near` can be a market name or id, a postal code (a prefix works), words in a market's name or city
    ("Köln"), or coordinates "50.94,6.96". No geocoder is used: a place without a toom market is given as a
    postal code of a nearby market or as coordinates.
    """
    if not 0 < radius_km <= 300:
        raise ToolError("radius_km must be between 0 and 300")
    _check_limit(limit)
    markets = await _all_markets()
    try:
        lat, lon, label = geo.resolve_center(markets, near)
    except ValueError as e:
        raise ToolError(str(e)) from e
    found = geo.within(markets, lat, lon, radius_km)
    return {
        "centre": {"lat": round(lat, 4), "lon": round(lon, 4), "understood_as": label},
        "radius_km": radius_km,
        "total": len(found),
        "markets": [{**m.public(), "distance_km": round(d, 1)} for d, m in found[:limit]],
    }


@mcp.tool
async def find_article_nearby(
    near: str,
    sku: str | None = None,
    quantity: int = 1,
    items: list[dict[str, Any]] | None = None,
    radius_km: float = 50,
    max_markets: int = 25,
) -> dict[str, Any]:
    """Where in the radius around a place can an article (or several) be picked up right now?

    Checks the real stock of every toom market within `radius_km` (straight-line, nearest first; at most
    `max_markets` markets, one request each) and returns them nearest first with the stock per article.
    Give one article via `sku` + `quantity`, or several via `items=[{"sku": "...", "quantity": 2}]`.
    `near` is understood as in markets_nearby. Markets that have everything are marked `complete`;
    `nearest_complete` is the one to drive to. Stock is live and can change, so call the market before a
    long drive (the phone number is included). Nothing is reserved or put in a basket.
    """
    if not 0 < radius_km <= 300:
        raise ToolError("radius_km must be between 0 and 300")
    if not 1 <= max_markets <= 40:
        raise ToolError("max_markets must be between 1 and 40")
    if items:
        wanted = _merge_items(items)
    elif sku:
        wanted = [{"sku": str(sku).strip(), "quantity": _int_quantity(quantity)}]
    else:
        raise ToolError("Give sku or items.")

    markets = await _all_markets()
    try:
        lat, lon, label = geo.resolve_center(markets, near)
    except ValueError as e:
        raise ToolError(str(e)) from e
    found = geo.within(markets, lat, lon, radius_km)
    if not found:
        raise ToolError(f"No toom market within {radius_km:g} km of '{near}' ({label}).")
    checked, skipped = found[:max_markets], found[max_markets:]

    titles: dict[str, str] = {}
    for w in wanted:  # also validates the article numbers before any stock request is made
        art = await _call(_session(checked[0][1]).article(w["sku"]))
        w["sku"] = art["sku"]
        titles[art["sku"]] = art["title"]

    rows = []
    for dist, m in checked:
        row: dict[str, Any] = {**m.public(), "distance_km": round(dist, 1)}
        try:
            got = await _session(m).stock(wanted)
        except ToomError as e:
            rows.append({**row, "complete": False, "error": str(e)})
            continue
        stock = {w["sku"]: got.get(w["sku"], 0) for w in wanted}
        short = [
            {"sku": w["sku"], "title": titles[w["sku"]], "wanted": w["quantity"], "available": stock[w["sku"]]}
            for w in wanted if stock[w["sku"]] < w["quantity"]
        ]
        rows.append({**row, "complete": not short, "stock": stock, "short": short})
    rows.sort(key=lambda r: (not r.get("complete"), r["distance_km"]))  # complete markets first, nearest first
    complete = [r for r in rows if r.get("complete")]
    return {
        "centre": {"lat": round(lat, 4), "lon": round(lon, 4), "understood_as": label},
        "radius_km": radius_km,
        "articles": [{"sku": w["sku"], "title": titles[w["sku"]], "quantity": w["quantity"]} for w in wanted],
        "markets_in_radius": len(found),
        "checked": len(checked),
        "not_checked": [f"{m.name} ({d:.0f} km)" for d, m in skipped],
        "nearest_complete": complete[0] if complete else None,
        "markets": rows,
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


MAX_ITEMS = 10  # articles per multi-article call: every article costs requests
MAX_ALSO_CHECK = 10
MAX_LIMIT = 100


def _int_quantity(value: Any, default: int = 1) -> int:
    """A whole number >= 1 from tool input, else a ToolError (never a raw TypeError/ValueError)."""
    v = default if value is None else value
    if isinstance(v, bool):
        raise ToolError("quantity must be a whole number >= 1")
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, str) and v.strip().isdigit():
        v = int(v.strip())
    if not isinstance(v, int) or v < 1:
        raise ToolError("quantity must be a whole number >= 1")
    return v


def _check_limit(limit: int, name: str = "limit") -> int:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
        raise ToolError(f"{name} must be between 1 and {MAX_LIMIT}")
    return limit


def _merge_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """[{"sku","quantity"}] -> one entry per article (quantities of duplicates are summed)."""
    if len(items) > MAX_ITEMS:
        raise ToolError(f"At most {MAX_ITEMS} articles per call (got {len(items)}).")
    merged: dict[str, dict[str, Any]] = {}
    for i in items:
        if not isinstance(i, dict) or not str(i.get("sku", "")).strip():
            raise ToolError('each item needs "sku"')
        sku = str(i["sku"]).strip()
        entry = merged.setdefault(sku, {"sku": sku, "quantity": 0, "title": i.get("title")})
        entry["quantity"] += _int_quantity(i.get("quantity"))
    return list(merged.values())


async def _availability(session: Toom, sku: str, title: str) -> dict[str, bool] | None:
    """Best effort: decode toom's availability code for the session's market from a search of the title."""
    try:
        hits = (await session.search(title))["products"]
    except ToomError:
        return None
    return next((h["availability"] for h in hits if h["sku"] == sku), None)


async def _lines(src_market: str | None, items: list[dict[str, Any]] | None) -> tuple[Market | None, list[dict[str, Any]]]:
    """Articles to work with: explicit `items` ([{"sku","quantity"}]) or else the basket of a market."""
    if items:
        return None, _merge_items(items)
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
