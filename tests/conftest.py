"""A fake toom.de built on httpx.MockTransport, answering with shapes recorded from the real site.

Fixtures (tests/fixtures): a real search page payload, a real basket line item, and a subset of the
real market list. Baskets are kept per market cookie, like the real server keeps them per session.
Behaviour that was NOT verified against the real site is marked ASSUMPTION.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from toomy import client as toom_client
from toomy import server

FIX = Path(__file__).parent / "fixtures"
TEMPLATE = json.loads((FIX / "basket_template.json").read_text(encoding="utf-8"))


def _article_state() -> dict[str, Any]:
    """The real product-page state of the door 110013, decoded from the recorded page."""
    import re
    from urllib.parse import unquote

    page = (FIX / "article_110013.html").read_text(encoding="utf-8")
    return json.loads(unquote(re.search(r'data-props="([^"]+)"', page).group(1)))


ARTICLE_STATE = _article_state()
DOOR = "110013"


def article_page(sku: str, title: str, price: float, url: str) -> str:
    """A product page like toom.de's: <title>, <h1> and the URL-encoded `data-props` state."""
    from urllib.parse import quote

    state = copy.deepcopy(ARTICLE_STATE)
    if sku != DOOR:  # other articles: same shape, own identity
        state["content"]["basic_info"].update(sku=sku, name=title)
        state["content"]["meta_data"].update(meta_price=price, canonical_url=url, title=title)
    enc = quote(json.dumps(state, ensure_ascii=False, separators=(",", ":")), safe="-_.!~*'()")
    safe_title = title.replace("&", "&amp;").replace("'", "&#039;")
    return (
        f"<html><head><title>Marke {safe_title} | toom Baumarkt</title></head><body>"
        f'<div id="root" data-props="{enc}"></div><h1>{safe_title}</h1></body></html>'
    )


def euro(v: float) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + "\xa0€"


class FakeToom:
    def __init__(self) -> None:
        self.markets = json.loads((FIX / "markets.json").read_text(encoding="utf-8"))
        self.search_html = (FIX / "search.html").read_text(encoding="utf-8")
        self.catalog = {p["sku"]: p for p in toom_client.parse_search_payload(self.search_html)["products"]}
        meta = ARTICLE_STATE["content"]["meta_data"]  # the door is orderable like any catalog article
        self.catalog[DOOR] = {
            "sku": DOOR,
            "title": ARTICLE_STATE["content"]["basic_info"]["name"],
            "price": "249,99\xa0€",
            "price_eur": meta["meta_price"],
            "url": meta["canonical_url"],
        }
        self.stock: dict[int, dict[str, int]] = {}  # market_id -> sku -> qty; missing sku = 10 everywhere
        self.baskets: dict[int, list[dict[str, Any]]] = {}  # market_id -> [{line_id, sku, quantity, delivery}]
        self.next_line = 100
        self.shipping_cost = 39.95
        self.freight_skus = {DOOR}
        self.free_shipping_from = 59
        self.recommendations = json.loads((FIX / "recommendations_110013.json").read_text(encoding="utf-8"))
        self.api_requests: list[tuple[str, dict[str, str], dict[str, str]]] = []  # path, query, headers
        self.requests: list[tuple[str, str, int | None]] = []
        self.queries: list[tuple[str, int | None]] = []
        self.search_qs: list[str] = []  # raw query string of each search request (filter/sort params)

    # ------------------------------------------------------------ helpers
    def stock_of(self, market: int, sku: str) -> int:
        return self.stock.get(market, {}).get(sku, 10)

    def set_stock(self, market: int, sku: str, qty: int) -> None:
        self.stock.setdefault(market, {})[sku] = qty

    def basket_json(self, market: int) -> dict[str, Any]:
        b = copy.deepcopy(TEMPLATE["basket"])
        lines, total = [], 0.0
        for row in self.baskets.get(market, []):
            p = self.catalog[row["sku"]]
            li = copy.deepcopy(TEMPLATE["line_template"])
            li["lineItemId"], li["quantity"], li["deliveryType"] = row["line_id"], row["quantity"], row["delivery"]
            li["product"].update(articleId=row["sku"], headline=p["title"], link=p["url"])
            li["product"]["price"]["value"] = p["price"]
            line_total = (p["price_eur"] or 0) * row["quantity"]
            li["sum"] = euro(line_total)
            li["reserveBuyBox"]["text"] = f"Verfügbar im Markt {market}"
            li["reserveBuyBox"]["status"] = "Verfügbar" if self.stock_of(market, row["sku"]) >= row["quantity"] else "Eingeschränkt"
            total += line_total
            lines.append(li)
        shipping = 0.0
        delivered = [r for r in self.baskets.get(market, []) if r["delivery"] == "homeDelivery_homeDelivery"]
        # the free-shipping limit is for parcels; bulky goods (the door) ship by freight and always pay
        if delivered and (total < self.free_shipping_from or any(r["sku"] in self.freight_skus for r in delivered)):
            shipping = self.shipping_cost
        b["settings"] = {"delivery": {"parcelFreeShippingThreshold": self.free_shipping_from}}
        b.update(
            lineItems=lines,
            totalQuantity=sum(r["quantity"] for r in self.baskets.get(market, [])),
            totalShippingCost=euro(shipping),
            totalCost=euro(total + shipping),
        )
        for li in b["lineItems"]:  # text of the delivery buy box, as the real site words it
            li["deliverBuyBox"] = {**li.get("deliverBuyBox", {}), "text": "Lieferzeit: ca. 4-8 Werktage", "status": "Verfügbar"}
        return b

    def add(self, market: int, sku: str, qty: int, delivery: str) -> httpx.Response | None:
        have = self.stock_of(market, sku)
        deliverable = delivery == "homeDelivery_homeDelivery" and sku in self.catalog  # real site: out of stock is fine
        if sku not in self.catalog or (have == 0 and not deliverable):
            return httpx.Response(404, json=[{"type": "error", "message": {"header": "", "body": "Product that you are trying to add is not available."}}])
        rows = self.baskets.setdefault(market, [])
        row = next((r for r in rows if r["sku"] == sku and r["delivery"] == delivery), None)
        if row is None:
            self.next_line += 1
            row = {"line_id": self.next_line, "sku": sku, "quantity": 0, "delivery": delivery}
            rows.append(row)
        # ASSUMPTION: the real shop caps a quantity above stock; only the 'Verfügbar' status is asserted on
        row["quantity"] += qty
        return None

    # ------------------------------------------------------------ transport
    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        cookie = request.headers.get("cookie", "")
        m = re.search(r"market_id=(\d+)", cookie)
        market = int(m.group(1)) if m else None
        self.requests.append((request.method, path, market))
        body = json.loads(request.content) if request.content else None

        if path.startswith("/public/api/recommendations/"):
            self.api_requests.append((path, dict(request.url.params), dict(request.headers)))
            if request.headers.get("x-client-id") != "pdp-browser" or request.url.host != "api.toom.de":
                return httpx.Response(404, text="404 page not found")
            kind = path.rsplit("/", 1)[-1]
            if kind not in ("also-bought", "similar"):
                return httpx.Response(404, text="404 page not found")
            return httpx.Response(200, json=self.recommendations[kind], headers={"x-recommendation-source": "Bondaten"} if kind == "also-bought" else {})
        if path == "/public/api/markets":
            return httpx.Response(200, json=self.markets)
        if path.startswith("/s/"):
            self.queries.append((unquote(path[3:]), market))
            self.search_qs.append(request.url.query.decode())
            if unquote(path[3:]) in self.catalog:  # real site: an exact article number redirects to the product page
                return httpx.Response(302, headers={"location": self.catalog[unquote(path[3:])]["url"]})
            return httpx.Response(200, text=self.search_html)
        if path.startswith("/p/"):
            sku = path.rstrip("/").rsplit("/", 1)[-1]
            p = self.catalog.get(sku)
            if p is None:
                return httpx.Response(404, text="not found")
            return httpx.Response(200, text=article_page(sku, p["title"], p["price_eur"], p["url"]))
        if market is None:
            return httpx.Response(400, text="no market cookie")

        base = "/shop/rest/V1/toom/basket"
        if path == base and request.method == "GET":
            # the real server issues the session cookie on the first basket request
            return httpx.Response(200, json=self.basket_json(market), headers={"set-cookie": "PHPSESSID=abc123; Path=/; Secure; HttpOnly"})
        if path == f"{base}/items" and request.method == "POST":
            err = self.add(market, str(body["articleId"]), body["quantity"], body["deliveryType"])
            return err or httpx.Response(200, json=self.basket_json(market))
        if path == f"{base}/multiple-items" and request.method == "POST":
            if not isinstance(body, list):  # real server: dict -> "articleId missing"
                return httpx.Response(400, json=[{"type": "error", "message": {"body": "Required parameter 'articleId' missing from payload"}}])
            for it in body:
                err = self.add(market, str(it["articleId"]), it["quantity"], it["deliveryType"])
                if err:
                    return err
            return httpx.Response(200, json=self.basket_json(market))
        mm = re.fullmatch(rf"{base}/items/update/(\d+)", path)
        if mm and request.method == "POST":
            for r in self.baskets.get(market, []):
                if r["line_id"] == int(mm.group(1)):
                    r["quantity"] = body["quantity"]
            return httpx.Response(200, json=self.basket_json(market))
        mm = re.fullmatch(rf"{base}/items/(\d+)", path)
        if mm and request.method == "DELETE":
            self.baskets[market] = [r for r in self.baskets.get(market, []) if r["line_id"] != int(mm.group(1))]
            return httpx.Response(200, json=self.basket_json(market))
        if path == "/shop/rest/V1/toom/stocks/availability" and request.method == "POST":
            if any("quantity" not in i for i in body):
                return httpx.Response(400, json=[{"type": "error", "message": {"body": "Required parameter 'quantity' missing from payload"}}])
            return httpx.Response(200, json=[
                {"articleId": i["articleId"], "deliveryType": i["deliveryType"], "availableQuantity": min(self.stock_of(market, i["articleId"]), i["quantity"])}
                for i in body
            ])
        return httpx.Response(404, text="not found")


@pytest.fixture
def toom(monkeypatch: pytest.MonkeyPatch) -> FakeToom:
    fake = FakeToom()
    monkeypatch.setattr(server, "_transport", httpx.MockTransport(fake.handler))
    monkeypatch.setattr(server, "_markets", None)
    monkeypatch.setattr(server, "_current", None)
    monkeypatch.setattr(server, "_sessions", {})
    monkeypatch.setattr(toom_client, "MIN_INTERVAL", 0)
    return fake


@pytest.fixture
async def mcp(toom: FakeToom):
    async with Client(server.mcp) as c:
        yield c


async def call(mcp: Client, tool: str, **args: Any) -> Any:
    return (await mcp.call_tool(tool, args)).data


async def call_err(mcp: Client, tool: str, **args: Any) -> str:
    with pytest.raises(ToolError) as e:
        await mcp.call_tool(tool, args)
    return str(e.value)
