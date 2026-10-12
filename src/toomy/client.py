"""Thin client for toom.de's own (undocumented) web endpoints.

One `Toom` object is one anonymous browser-like session bound to one market (toom calls its stores
"Märkte"). The basket lives server-side in that session, so two sessions == two independent baskets,
which is what makes copying a basket from one market to another possible.

Be a polite guest: calls are throttled, and search only reads the first result page (toom.de's
robots.txt disallows `/s/*?page=`).
"""

from __future__ import annotations

import asyncio
import html
import json
import math
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, unquote

import httpx

BASE_URL = "https://toom.de"
PICKUP = "marketPickup_marketPickup"  # reserve online, collect in the market
DELIVERY = "homeDelivery_homeDelivery"  # parcel / home delivery
DELIVERY_TYPES = {"pickup": PICKUP, "delivery": DELIVERY}

USER_AGENT = "toomy/0.1 (+https://github.com/tronicum/toomy; personal assistant, low request rate)"
MARKET_COOKIES = ("market_id", "market_name")

# --- search filters/sorting, as the toom.de search page itself puts them into the URL (observed) -------------
OR_SEPARATOR = "~~~"  # several values of one filter: brands:Bosch~~~Makita
FILTER_OFFERS = "inv(attr.dp_angebot)"  # "Nur Angebote anzeigen"
FILTER_PRICE = "inv(price)"  # price slider, value "[min,max]" (both bounds are required)
AVAILABILITY_FILTERS = {  # the page's "Verfügbarkeit" options 0/1/2, each filtered by market id
    "0": "customFulfillment1",  # home delivery
    "1": "pickupInStore",  # in stock in the market
    "2": "shipToStore",  # not in stock, orderable by the market ("Artikel anfragen")
}
SORTS = {
    "relevance": None,
    "price_asc": "inv(price)",
    "price_desc": "inv(price):desc",
    "offers": "inv(attr.dp_angebot)",  # offers first
    "rating": "rating:desc",  # best rated first (toom's plain "rating" sorts worst first)
}
MAX_PRICE = 1_000_000
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
    lat: float | None = None
    lon: float | None = None

    @classmethod
    def from_api(cls, m: dict[str, Any]) -> "Market":
        a = m.get("address") or {}
        lat, lon = _coordinates(a.get("latitude"), a.get("longitude"))
        return cls(
            id=int(m["id"]),
            name=_text(m["name"]),
            city=_text(a.get("city")),
            zip=_text(a.get("zip")),
            street=_text(a.get("street")),
            phone=_text(m.get("phone")),
            link=f"{BASE_URL}/{_text(m.get('link')).lstrip('/')}",
            lat=lat,
            lon=lon,
        )

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "address": f"{self.street}, {self.zip} {self.city}", "phone": self.phone, "url": self.link}


def _to_float(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _coordinates(lat: Any, lon: Any) -> tuple[float | None, float | None]:
    """Valid coordinates or (None, None); 0,0 is a placeholder, not a place in Germany."""
    la, lo = _to_float(lat), _to_float(lon)
    if la is None or lo is None or not (-90 <= la <= 90 and -180 <= lo <= 180) or (la == 0 and lo == 0):
        return None, None
    return la, lo


def _text(v: Any) -> str:
    return "" if v is None else str(v)


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


def parse_product_page(html_text: str) -> str | None:
    """Article title from a toom.de product page (h1, else og:title, else <title> without the shop suffix)."""
    for pat in (r"<h1[^>]*>(.*?)</h1>", r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', r"<title[^>]*>(.*?)</title>"):
        m = re.search(pat, html_text, re.S)
        if m:
            title = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
            title = re.sub(r"\s*\|\s*toom Baumarkt\s*$", "", title)
            if title:
                return title
    return None


def build_search_query(filters: dict[str, list[str]] | None = None, sort: str | None = None) -> str:
    """Query string for /s/<term>: one `filter=<param>:<values>` per filter (AND), `sort=<key>`.

    Built by hand with %20 and never "+": toom's robots.txt disallows `/s/*+`. `page` is never set.
    Values that look like a range ("[50,150]") are passed verbatim; several values are OR-ed.
    """
    parts = []
    for param, values in (filters or {}).items():
        vals = [str(v) for v in values if str(v) != ""]
        if not vals:
            continue
        joined = vals[0] if (len(vals) == 1 and vals[0].startswith("[") and vals[0].endswith("]")) else OR_SEPARATOR.join(vals)
        parts.append("filter=" + quote(f"{param}:{joined}", safe=""))
    if sort:
        parts.append("sort=" + quote(sort, safe=""))
    return "&".join(parts)


def parse_filters(raw_filters: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Normalise the filters toom offers for a search (the category tree is left out)."""
    out: list[dict[str, Any]] = []
    for f in raw_filters or []:
        style, param = f.get("style"), f.get("paramName")
        if style == "MULTISELECT":
            values = [
                {"value": v["name"], "label": v.get("displayName") or v["name"], "count": v.get("productsCount", 0)}
                for v in f.get("multiselectValues", [])
            ]
            out.append({"name": f["name"], "param": param, "type": "choice", "values": values})
        elif style == "SLIDER":
            sv = f.get("sliderValues") or {}
            out.append({"name": f["name"], "param": param, "type": "range", "min": sv.get("absoluteMin"), "max": sv.get("absoluteMax"), "unit": f.get("unit", "")})
    return out


def _split_values(value: Any) -> Any:
    """toom joins multi-valued attributes with '|' ("Grau|Weiß")."""
    return value.split("|") if isinstance(value, str) and "|" in value else value


def parse_article_state(html_text: str) -> dict[str, Any]:
    """Details of an article from the state a toom.de product page embeds (`data-props`, URL-encoded JSON)."""
    m = re.search(r'<div id="root" data-props="([^"]+)"', html_text)
    if not m:
        raise ToomError("Product page has no embedded data; toom.de may have changed its frontend.")
    try:
        return _article_from_state(json.loads(unquote(m.group(1))))
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise ToomError("Product page data could not be read; toom.de may have changed its frontend.") from e


def _article_from_state(state: dict[str, Any]) -> dict[str, Any]:
    c = state["content"]
    if not isinstance(c, dict) or not isinstance(c.get("basic_info"), dict):
        raise KeyError("content.basic_info")
    basic = c["basic_info"]
    details = c.get("details") if isinstance(c.get("details"), dict) else {}
    meta = c.get("meta_data") if isinstance(c.get("meta_data"), dict) else {}
    if not (basic.get("sku") or basic.get("name") or meta.get("title")):
        raise KeyError("article has neither sku nor title")
    chars = c.get("characteristics") if isinstance(c.get("characteristics"), dict) else {}
    groups: dict[str, dict[str, Any]] = {}
    for group, items in chars.items():
        if group in ("produktdetails", "herstellerinformationen") or not isinstance(items, list):
            continue  # produktdetails repeats technische-details
        pairs = {
            i["label"]: _split_values(i["value"])
            for i in items
            if isinstance(i, dict) and i.get("label") and i.get("value") not in (None, "")
        }
        if pairs:
            groups[group] = pairs
    maker = chars.get("herstellerinformationen") if isinstance(chars.get("herstellerinformationen"), dict) else {}
    manufacturer = {k: (maker.get(k) or {}).get("value") if isinstance(maker.get(k), dict) else None for k in ("name", "street", "phone", "email")}
    return {
        "sku": basic.get("sku"),
        "title": html.unescape(basic.get("name") or meta.get("title") or ""),
        "brand": (details.get("brand") or {}).get("name") if isinstance(details.get("brand"), dict) else None,
        "price_eur": meta.get("meta_price"),
        "ean": meta.get("ean") or None,
        "url": meta.get("canonical_url") or None,
        "summary": [str(x) for x in ((details.get("selling_points") or {}).get("items") or [])]
        if isinstance(details.get("selling_points"), dict)
        else [],
        "description": basic.get("description") or None,
        "characteristics": groups,
        "manufacturer": manufacturer if any(manufacturer.values()) else None,
        "categories": [b["name"] for b in c.get("breadcrumb") or [] if isinstance(b, dict) and b.get("name")],
        "images": [g["src"] for g in c.get("gallery") or [] if isinstance(g, dict) and g.get("src")][:6],
        "documents": c.get("documents") or [],
        "variants": c.get("variants"),
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
        return {"count": 0, "products": [], "filters": []}
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
            "on_offer": bool((p.get("flags") or {}).get("aktion") or _undefined((p.get("flags") or {}).get("promotionLabel"))),
            "promotion": _undefined((p.get("flags") or {}).get("promotionLabel")),  # e.g. "Gratis Beigabe"
            "availability_code": p.get("availabilityCode"),  # raw, as toom sends it
            "availability": decode_availability(p.get("availabilityCode")),
            "low_stock": p.get("lowStockAvailability"),
        })
    return {
        "count": obj["products"].get("count", len(products)),
        "products": products,
        "filters": parse_filters(obj.get("filters")),
    }


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

    @staticmethod
    def _json(r: httpx.Response) -> Any:
        try:
            return r.json()
        except ValueError as e:  # an HTML bot-check or maintenance page with status 200
            raise ToomError(
                f"toom.de answered with something that is not JSON (HTTP {r.status_code}); it may be blocking "
                "requests or under maintenance."
            ) from e

    # ------------------------------------------------------------------ search
    async def search(
        self, query: str, filters: dict[str, list[str]] | None = None, sort: str | None = None
    ) -> dict[str, Any]:
        """First result page only (up to ~72 hits) for the session's market, optionally filtered/sorted."""
        qs = build_search_query(filters, sort)
        path = f"/s/{quote(query.strip(), safe='')}" + (f"?{qs}" if qs else "")
        r = await self._request("GET", path, headers={"Accept": "text/html"})
        if r.url.path.startswith("/p/"):  # an exact article number redirects to the product page
            raise ToomError(f"'{query}' matched exactly one article and toom.de sent its product page ({r.url}); search by words instead.")
        return parse_search_payload(r.text)

    async def product(self, sku: str) -> dict[str, Any]:
        """Look an article up by its number: toom redirects /s/<number> to the product page."""
        r = await self._request("GET", f"/s/{quote(str(sku).strip(), safe='')}", headers={"Accept": "text/html"})
        if not r.url.path.startswith("/p/"):
            raise ToomError(f"No article with the number '{sku}' found on toom.de.")
        title = parse_product_page(r.text)
        if not title:
            raise ToomError(f"Found the product page for '{sku}' but could not read its title.")
        return {"sku": str(sku).strip(), "title": title, "url": str(r.url)}

    async def article(self, sku: str) -> dict[str, Any]:
        """All details of an article by number (one product page request)."""
        r = await self._request("GET", f"/s/{quote(str(sku).strip(), safe='')}", headers={"Accept": "text/html"})
        if not r.url.path.startswith("/p/"):
            raise ToomError(f"No article with the number '{sku}' found on toom.de.")
        art = parse_article_state(r.text)
        art["sku"] = str(art.get("sku") or sku).strip()
        art["url"] = art.get("url") or str(r.url)
        return art

    # ------------------------------------------------------------------ basket
    async def basket(self) -> dict[str, Any]:
        return parse_basket(self._json(await self._request("GET", "/shop/rest/V1/toom/basket")))

    async def add(self, sku: str, quantity: int = 1, delivery_type: str = PICKUP) -> dict[str, Any]:
        body = {"articleId": sku, "quantity": quantity, "deliveryType": delivery_type}
        return parse_basket(self._json(await self._request("POST", "/shop/rest/V1/toom/basket/items", json=body)))

    async def add_many(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Adds to whatever is already in the basket (does not replace)."""
        body = [{"articleId": i["sku"], "quantity": i["quantity"], "deliveryType": i["delivery_type"]} for i in items]
        return parse_basket(self._json(await self._request("POST", "/shop/rest/V1/toom/basket/multiple-items", json=body)))

    async def set_quantity(self, line_id: int, quantity: int) -> dict[str, Any]:
        body = {"quantity": quantity}
        return parse_basket(self._json(await self._request("POST", f"/shop/rest/V1/toom/basket/items/update/{line_id}", json=body)))

    async def remove(self, line_id: int) -> dict[str, Any]:
        return parse_basket(self._json(await self._request("DELETE", f"/shop/rest/V1/toom/basket/items/{line_id}")))

    async def stock(self, items: list[dict[str, Any]]) -> dict[str, int]:
        """Stock of the session's market per article: {sku: availableQuantity} (capped at the asked quantity).

        One request for many articles, no basket involved. Pickup only: that is what market stock means.
        """
        if not items:
            return {}
        body = [{"articleId": i["sku"], "quantity": i["quantity"], "deliveryType": PICKUP} for i in items]
        r = await self._request("POST", "/shop/rest/V1/toom/stocks/availability", json=body)
        data = self._json(r)
        try:
            return {str(x["articleId"]): int(x.get("availableQuantity") or 0) for x in data}
        except (TypeError, KeyError, ValueError, AttributeError) as e:
            raise ToomError("toom.de's stock answer had an unexpected shape.") from e

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
    try:
        raw = r.json()["markets"]
        assert isinstance(raw, list)
    except (ValueError, KeyError, TypeError, AssertionError) as e:
        raise ToomError("The market list had an unexpected shape.") from e
    out = []
    for m in raw:  # one malformed entry must not take every market-based tool down
        try:
            out.append(Market.from_api(m))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return out


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
