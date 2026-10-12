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


def euro(v: float) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + "\xa0€"


class FakeToom:
    def __init__(self) -> None:
        self.markets = json.loads((FIX / "markets.json").read_text(encoding="utf-8"))
        self.search_html = (FIX / "search.html").read_text(encoding="utf-8")
        self.catalog = {p["sku"]: p for p in toom_client.parse_search_payload(self.search_html)["products"]}
        self.stock: dict[int, dict[str, int]] = {}  # market_id -> sku -> qty; missing sku = 10 everywhere
        self.baskets: dict[int, list[dict[str, Any]]] = {}  # market_id -> [{line_id, sku, quantity, delivery}]
        self.next_line = 100
        self.requests: list[tuple[str, str, int | None]] = []
        self.queries: list[tuple[str, int | None]] = []

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
        b.update(lineItems=lines, totalQuantity=sum(r["quantity"] for r in self.baskets.get(market, [])), totalCost=euro(total))
        return b

    def add(self, market: int, sku: str, qty: int, delivery: str) -> httpx.Response | None:
        have = self.stock_of(market, sku)
        if sku not in self.catalog or have == 0:
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

        if path == "/public/api/markets":
            return httpx.Response(200, json=self.markets)
        if path.startswith("/s/"):
            self.queries.append((unquote(path[3:]), market))
            if unquote(path[3:]) in self.catalog:  # real site: an exact article number redirects to the product page
                return httpx.Response(302, headers={"location": self.catalog[unquote(path[3:])]["url"]})
            return httpx.Response(200, text=self.search_html)
        if path.startswith("/p/"):
            sku = path.rstrip("/").rsplit("/", 1)[-1]
            title = self.catalog[sku]["title"].replace("'", "&#039;") if sku in self.catalog else "Unknown"
            return httpx.Response(
                200,
                text=f"<html><head><title>Marke {title} | toom Baumarkt</title></head><body><h1>{title}</h1></body></html>",
            )
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
