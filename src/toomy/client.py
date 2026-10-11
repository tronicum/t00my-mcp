"""Thin client for toom.de's own (undocumented) web endpoints.

One `Toom` object is one anonymous browser-like session bound to one market (toom calls its stores
"Märkte"). The basket lives server-side in that session, so two sessions == two independent baskets,
which is what makes copying a basket from one market to another possible.

Be a polite guest: calls are throttled, and search only reads the first result page (toom.de's
robots.txt disallows `/s/*?page=`).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

BASE_URL = "https://toom.de"
PICKUP = "marketPickup_marketPickup"  # reserve online, collect in the market
DELIVERY = "homeDelivery_homeDelivery"  # parcel / home delivery
DELIVERY_TYPES = {"pickup": PICKUP, "delivery": DELIVERY}

USER_AGENT = "toomy/0.1 (+https://github.com/tronicum/toomy; personal assistant, low request rate)"
MARKET_COOKIES = ("market_id", "market_name")
MIN_INTERVAL = 0.5  # seconds between any two requests this process makes

_throttle_lock = asyncio.Lock()
_last_request = 0.0


class ToomError(Exception):
    """A request to toom.de failed or returned something unexpected."""


async def _throttle() -> None:
    global _last_request
    async with _throttle_lock:
        wait = _last_request + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request = time.monotonic()


@dataclass(frozen=True)
class Market:
    id: int
    name: str
    city: str
    zip: str
    street: str
    phone: str
    link: str

    @classmethod
    def from_api(cls, m: dict[str, Any]) -> "Market":
        a = m.get("address") or {}
        return cls(
            id=int(m["id"]),
            name=m["name"],
            city=a.get("city", ""),
            zip=a.get("zip", ""),
            street=a.get("street", ""),
            phone=m.get("phone", ""),
            link=f"{BASE_URL}/{m.get('link', '').lstrip('/')}",
        )

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "address": f"{self.street}, {self.zip} {self.city}", "phone": self.phone, "url": self.link}


def _price_to_float(text: str | None) -> float | None:
    """'1.234,56 €' -> 1234.56"""
    if not text:
        return None
    m = re.search(r"\d[\d.]*(?:,\d+)?", text)
    return float(m.group(0).replace(".", "").replace(",", ".")) if m else None


def decode_availability(code: Any) -> dict[str, bool]:
    """Decode toom's availabilityCode as the toom.de product page does ("0|1|2", "0|2", "1", "2", ...).

    Digits: 0 = home delivery, 1 = available in the market (can be reserved for pickup),
    2 = "orderable in the market" ("Bestellbar in <Markt>"). Anything else (e.g. "unavailable") = nothing.
    """
    digits = set(str(code or "").split("|"))
    return {
        "home_delivery": "0" in digits,
        "market_reservable": "1" in digits,
        "market_orderable": "2" in digits,
    }


def _undefined(v: Any) -> Any:
    return None if v == "$undefined" else v


def parse_search_payload(html: str) -> dict[str, Any]:
    """Pull the product list out of the Next.js server payload embedded in a /s/<query> page."""
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', html, re.S)
    if not chunks:
        raise ToomError("Search page has no embedded data; toom.de may have changed its frontend.")
    text = "".join(json.loads(f'"{c}"') for c in chunks)
    start = text.find('{"products":{"list":')
    if start < 0:
        return {"count": 0, "products": []}
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    products = []
    for p in obj["products"]["list"]:
        price = (p.get("price") or {}).get("value") or ""
        brand = p.get("brand") or {}
        link = (p.get("link") or {}).get("url") or ""
        rating = p.get("ratingData") or {}
        products.append({
            "sku": p["sku"],
            "title": p.get("headline"),
            "brand": brand.get("text") if isinstance(brand, dict) else None,
            "price": price,
            "price_eur": _price_to_float(price),
            "url": f"{BASE_URL}{link}" if link.startswith("/") else link,
            "rating": _undefined(rating.get("rating")) if isinstance(rating, dict) else None,
            "reviews": rating.get("reviewsCount") if isinstance(rating, dict) else None,
            "on_offer": bool((p.get("flags") or {}).get("aktion")),
            "availability_code": p.get("availabilityCode"),  # raw, as toom sends it
            "availability": decode_availability(p.get("availabilityCode")),
            "low_stock": p.get("lowStockAvailability"),
        })
    return {"count": obj["products"].get("count", len(products)), "products": products}


def parse_basket(b: dict[str, Any]) -> dict[str, Any]:
    lines = []
    for li in b.get("lineItems", []):
        prod = li.get("product", {})
        res, dlv = li.get("reserveBuyBox") or {}, li.get("deliverBuyBox") or {}
        lines.append({
            "line_id": li["lineItemId"],
            "sku": prod.get("articleId"),
            "title": prod.get("headline"),
            "brand": prod.get("brand"),
            "url": prod.get("link"),
            "quantity": li["quantity"],
            "max_quantity": (li.get("numberSelect") or {}).get("max"),
            "delivery_type": li.get("deliveryType"),
            "unit_price": (prod.get("price") or {}).get("value"),
            "unit_price_eur": _price_to_float((prod.get("price") or {}).get("value")),
            "line_total": li.get("sum"),
            "pickup": {"status": res.get("status"), "text": res.get("text"), "hint": res.get("tooltip")},
            "delivery": {"status": dlv.get("status"), "text": dlv.get("text")},
        })
    return {
        "lines": lines,
        "total_quantity": b.get("totalQuantity", 0),
        "total": b.get("totalCost"),
        "total_eur": _price_to_float(b.get("totalCost")),
        "shipping": b.get("totalShippingCost"),
        "can_checkout": b.get("canCheckout"),
        "messages": b.get("messages", []),
        "changed_stock": b.get("changedStock", {}),
    }


class Toom:
    """An anonymous toom.de session bound to one market."""

    def __init__(self, market: Market, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.market = market
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            cookies=self._market_cookies(market),
            timeout=20,
            follow_redirects=True,
            transport=transport,
        )

    @staticmethod
    def _market_cookies(m: Market) -> httpx.Cookies:
        jar = httpx.Cookies()
        # the site stores these URL-encoded; availability and prices follow the market cookie
        jar.set("market_id", str(m.id), domain="toom.de")
        jar.set("market_name", quote(m.name), domain="toom.de")
        return jar

    async def aclose(self) -> None:
        await self._http.aclose()

    def export_cookies(self) -> list[dict[str, Any]]:
        """The session's cookies in the JSON format browser cookie managers (e.g. Cookie-Editor) import."""
        out = []
        for c in self._http.cookies.jar:
            # the site's own JavaScript reads the market cookies, so they must not be imported as HttpOnly
            # (httpx marks cookies it sets itself as HttpOnly, which would be wrong here)
            http_only = c.has_nonstandard_attr("HttpOnly") and c.name not in MARKET_COOKIES
            entry: dict[str, Any] = {
                "name": c.name,
                "value": c.value,
                "domain": c.domain or "toom.de",
                "path": c.path or "/",
                "secure": bool(c.secure),
                "httpOnly": http_only,
                "sameSite": "lax",
                "hostOnly": not (c.domain or "").startswith("."),
                "session": c.expires is None,
            }
            if c.expires is not None:
                entry["expirationDate"] = c.expires
            out.append(entry)
        return out

    async def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        await _throttle()
        try:
            r = await self._http.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise ToomError(f"toom.de request {method} {path} failed: {e}") from e
        if r.status_code >= 400:
            detail = r.text[:300]
            try:  # toom answers errors as [{"type":"error","message":{"body": "..."}}]
                detail = "; ".join(m["message"]["body"] for m in r.json())
            except Exception:
                pass
            raise ToomError(f"toom.de {method} {path} -> HTTP {r.status_code}: {detail}")
        return r

    # ------------------------------------------------------------------ search
    async def search(self, query: str) -> dict[str, Any]:
        """First result page only (up to ~72 hits) for the session's market."""
        r = await self._request("GET", f"/s/{quote(query.strip(), safe='')}", headers={"Accept": "text/html"})
        if r.url.path.startswith("/p/"):  # an exact article number redirects to the product page
            raise ToomError(f"'{query}' matched exactly one article and toom.de sent its product page ({r.url}); search by words instead.")
        return parse_search_payload(r.text)

    # ------------------------------------------------------------------ basket
    async def basket(self) -> dict[str, Any]:
        return parse_basket((await self._request("GET", "/shop/rest/V1/toom/basket")).json())

    async def add(self, sku: str, quantity: int = 1, delivery_type: str = PICKUP) -> dict[str, Any]:
        body = {"articleId": sku, "quantity": quantity, "deliveryType": delivery_type}
        return parse_basket((await self._request("POST", "/shop/rest/V1/toom/basket/items", json=body)).json())

    async def add_many(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Adds to whatever is already in the basket (does not replace)."""
        body = [{"articleId": i["sku"], "quantity": i["quantity"], "deliveryType": i["delivery_type"]} for i in items]
        return parse_basket((await self._request("POST", "/shop/rest/V1/toom/basket/multiple-items", json=body)).json())

    async def set_quantity(self, line_id: int, quantity: int) -> dict[str, Any]:
        body = {"quantity": quantity}
        return parse_basket((await self._request("POST", f"/shop/rest/V1/toom/basket/items/update/{line_id}", json=body)).json())

    async def remove(self, line_id: int) -> dict[str, Any]:
        return parse_basket((await self._request("DELETE", f"/shop/rest/V1/toom/basket/items/{line_id}")).json())

    async def stock(self, items: list[dict[str, Any]]) -> dict[str, int]:
        """Stock of the session's market per article: {sku: availableQuantity} (capped at the asked quantity).

        One request for many articles, no basket involved. Pickup only: that is what market stock means.
        """
        if not items:
            return {}
        body = [{"articleId": i["sku"], "quantity": i["quantity"], "deliveryType": PICKUP} for i in items]
        r = await self._request("POST", "/shop/rest/V1/toom/stocks/availability", json=body)
        return {str(x["articleId"]): int(x.get("availableQuantity") or 0) for x in r.json()}

    async def clear(self) -> dict[str, Any]:
        basket = await self.basket()
        for line in basket["lines"]:
            basket = await self.remove(line["line_id"])
        return basket


async def fetch_markets(transport: httpx.AsyncBaseTransport | None = None) -> list[Market]:
    await _throttle()
    async with httpx.AsyncClient(base_url=BASE_URL, headers={"User-Agent": USER_AGENT}, timeout=20, transport=transport) as c:
        try:
            r = await c.get("/public/api/markets")
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise ToomError(f"Could not load the market list: {e}") from e
    return [Market.from_api(m) for m in r.json()["markets"]]


def _fold(s: str) -> str:
    return s.casefold().replace("ß", "ss").replace("ä", "a").replace("ö", "o").replace("ü", "u")


def match_markets(markets: list[Market], query: str, limit: int = 10) -> list[Market]:
    """Find markets by id, postal code (prefix) or words in name/city (then street), umlaut-insensitive."""
    q = _fold(query.strip())
    if q.isdigit():
        exact = [m for m in markets if str(m.id) == q]
        if exact:
            return exact
        return [m for m in markets if m.zip.startswith(q)][:limit]
    words = q.split()
    hits = [m for m in markets if all(w in _fold(f"{m.name} {m.city}") for w in words)]
    if not hits:  # only fall back to street names when name/city say nothing ("Zeppelinstraße")
        hits = [m for m in markets if all(w in _fold(f"{m.name} {m.city} {m.street}") for w in words)]
    return hits[:limit]
