"""Pure planning logic: where to pick up which article, given the stock per market.

No I/O here, so it is easy to test. Priority: the primary market first (that is where the goods are needed);
fallback markets only for articles the primary market does not have and that are small enough to transport
without the special vehicle that is available at the primary market.
"""

from __future__ import annotations

from typing import Any


def plan_pickup(
    lines: list[dict[str, Any]],
    stock: dict[int, dict[str, int]],
    names: dict[int, str],
    fallback_markets: list[int],
    primary_market: int | None = None,
    bulky: set[str] | None = None,
) -> dict[str, Any]:
    """Assign every article to a market.

    lines:   [{"sku", "title", "quantity"}]
    stock:   {market_id: {sku: available quantity}}
    bulky:   skus that only fit the special vehicle at the primary market: they are NEVER taken from a fallback.
    Rules: 1) everything the primary market has is picked up there (up to the wanted quantity);
           2) the rest (not bulky) comes from fallback markets, in as few stops as possible: markets that
              have an article in full first, most articles first, otherwise the quantity is split;
           3) whatever is still short is reported, with the markets that do have it.
    Without a primary market this is plain "fewest stops over the given markets".
    """
    bulky = bulky or set()
    if bulky and primary_market is None:
        raise ValueError("bulky articles need a primary_market")
    want = {l["sku"]: l["quantity"] for l in lines}
    title = {l["sku"]: l.get("title") for l in lines}
    have = lambda m, sku: stock.get(m, {}).get(sku, 0)  # noqa: E731

    taken: dict[int, dict[str, int]] = {}  # market -> sku -> qty
    remaining = dict(want)

    def take(m: int, sku: str, qty: int) -> None:
        if qty > 0:
            taken.setdefault(m, {})
            taken[m][sku] = taken[m].get(sku, 0) + qty
            remaining[sku] -= qty

    # 1) primary market: everything it has
    if primary_market is not None:
        for sku, q in want.items():
            take(primary_market, sku, min(have(primary_market, sku), q))

    # 2) fallback: only non-bulky articles, fewest stops (full coverage first, then split)
    candidates = [m for m in fallback_markets if m != primary_market]
    loose = lambda: [s for s, q in remaining.items() if q > 0 and s not in bulky]  # noqa: E731
    while True:
        todo = loose()
        if not todo:
            break

        def score(m: int) -> tuple[int, int]:
            full = sum(1 for s in todo if have(m, s) >= remaining[s])
            return (full, 1 if m in taken else 0)  # most full matches, then markets that are already a stop

        best = max(candidates, key=lambda m: (score(m), -candidates.index(m)), default=None)
        if best is None or score(best)[0] == 0:
            break
        for s in todo:
            if have(best, s) >= remaining[s]:
                take(best, s, remaining[s])
    for s in loose():  # partial leftovers: split across fallback markets, richest stock first
        for m in sorted(candidates, key=lambda m: -have(m, s)):
            take(m, s, min(have(m, s) - taken.get(m, {}).get(s, 0), remaining[s]))
            if remaining[s] <= 0:
                break

    stops = [
        {
            "market": names[m],
            "market_id": m,
            "role": "primary" if m == primary_market else "fallback",
            "pick_up": [{"sku": s, "title": title[s], "quantity": q} for s, q in items.items()],
        }
        for m, items in taken.items()
    ]
    stops.sort(key=lambda st: (st["role"] != "primary", st["market"]))
    missing = [
        {
            "sku": s,
            "title": title[s],
            "wanted": want[s],
            "missing": q,
            "bulky": s in bulky,
            # where it IS in stock (any checked market), so the caller can decide what to do instead
            "in_stock_elsewhere": {names[m]: have(m, s) for m in stock if have(m, s) > 0 and have(m, s) > taken.get(m, {}).get(s, 0)},
        }
        for s, q in remaining.items() if q > 0
    ]
    return {
        "stops": stops,
        "complete": not missing,
        # one stop at the primary market covers everything: no fallback trip (and no own transport) needed
        "all_at_primary_market": not missing and bool(stops) and all(st["role"] == "primary" for st in stops),
        "missing": missing,
        "stock_matrix": {s: {names[m]: min(have(m, s), want[s]) for m in sorted(stock)} for s in want},
    }
