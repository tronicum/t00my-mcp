"""Opening hours of a toom market: weekly hours plus toom's special days. Pure; `now` is always passed in.

Data (as toom's market list gives it): hours per weekday and "special opening times" for single dates
(a 00:00-00:00 entry means closed that day, e.g. a public holiday). Holidays that toom did not enter are
not known here, so "open" is what toom's data says, not a promise.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from toomy.client import Market

WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
HORIZON_DAYS = 21


def _t(text: str, closing: bool = False) -> time | None:
    """"07:30" -> time; a closing time of 00:00 or 24:00 means the end of that day. Unparseable -> None."""
    try:
        h, m = (int(x) for x in str(text).split(":")[:2])
    except ValueError:
        return None
    if closing and (h, m) in ((0, 0), (24, 0)):
        return time.max
    return time(h, m) if 0 <= h <= 23 and 0 <= m <= 59 else None


def _window(h: tuple[str, str] | None) -> tuple[time, time] | None:
    """(opens, closes) as times, or None when closed or unreadable."""
    if not h:
        return None
    o, c = _t(h[0]), _t(h[1], closing=True)
    return (o, c) if o is not None and c is not None and o < c else None


def hours_on(market: Market, day: date) -> tuple[tuple[str, str] | None, bool]:
    """(opening, closing) or None if closed on that date, and whether a special entry decided it."""
    for entry_date, opening, closing in market.special:
        if entry_date == day:
            return ((opening, closing), True) if opening and closing and opening != closing else (None, True)
    return market.hours[day.weekday()], False


def _fmt(h: tuple[str, str] | None) -> str:
    return f"{h[0]}–{h[1]}" if h else "geschlossen"


def status(market: Market, now: datetime) -> dict[str, Any]:
    """Is the market open at `now` (timezone-aware, local time), today's hours, and when it opens next."""
    today, special = hours_on(market, now.date())
    open_now = False
    closes_at = None
    clock = now.time().replace(tzinfo=None)
    w = _window(today)
    if w and w[0] <= clock <= w[1] and not (clock == w[1] and w[1] != time.max):
        open_now, closes_at = True, today[1]
    next_open = None
    if not open_now:
        for offset in range(0, HORIZON_DAYS):
            d = now.date() + timedelta(days=offset)
            h, _ = hours_on(market, d)
            w = _window(h)
            if not w:
                continue
            if offset == 0 and clock >= w[0]:
                continue  # today's opening time has passed (and we are not inside the window: it closed)
            next_open = {"date": d.isoformat(), "weekday": WEEKDAYS[d.weekday()], "from": h[0], "until": h[1]}
            break
    return {
        "open_now": open_now,
        "closes_at": closes_at,
        "today": _fmt(today),
        "today_is_special_day": special,
        "next_open": next_open,
    }


def week(market: Market) -> list[dict[str, str]]:
    return [{"day": WEEKDAYS[i], "hours": _fmt(h)} for i, h in enumerate(market.hours)]


def upcoming_special_days(market: Market, today: date, days: int = 60) -> list[dict[str, str]]:
    out = []
    for d, opening, closing in sorted(market.special):
        if today <= d <= today + timedelta(days=days):
            h = (opening, closing) if opening and closing and opening != closing else None
            out.append({"date": d.isoformat(), "weekday": WEEKDAYS[d.weekday()], "hours": _fmt(h)})
    return out
