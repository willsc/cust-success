"""UK business-hours SLA clock: due dates, pause accounting and breach flags.

There is no native primitive for "8 business hours from now, UK time, skipping
bank holidays", so this module is it. Everything here works in Europe/London
(the business day is defined in local time and survives BST) and returns UTC ISO
strings, which is what the tickets table stores.

Three published calendars are supported because they genuinely differ — Scotland
has 2 January and no Easter Monday, Northern Ireland has St Patrick's Day and
the Battle of the Boyne. Each queue picks one in `tickets.QUEUE_CALENDAR`.

The dates below match gov.uk's published lists for 2024-2027. They are a
fallback: `refresh_from_govuk()` pulls the live list from
https://www.gov.uk/bank-holidays.json and caches it, which is the honest way to
stay right about substitute days in future years.
"""
import json
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

from . import db

LONDON = ZoneInfo("Europe/London")
UTC = ZoneInfo("UTC")

BUSINESS_START = time(9, 0)
BUSINESS_END = time(17, 0)
HOURS_PER_DAY = 8.0

GOVUK_URL = "https://www.gov.uk/bank-holidays.json"
CACHE_KEY = "_bank_holidays_cache"   # settings row, not a user-facing setting

CALENDARS = {
    "england-and-wales": {"label": "England & Wales"},
    "scotland": {"label": "Scotland"},
    "northern-ireland": {"label": "Northern Ireland"},
}
DEFAULT_CALENDAR = "england-and-wales"

# Fallback lists (gov.uk, 2024-2027). Substitute days are already applied.
STATIC_HOLIDAYS = {
    "england-and-wales": [
        "2024-01-01", "2024-03-29", "2024-04-01", "2024-05-06", "2024-05-27",
        "2024-08-26", "2024-12-25", "2024-12-26",
        "2025-01-01", "2025-04-18", "2025-04-21", "2025-05-05", "2025-05-26",
        "2025-08-25", "2025-12-25", "2025-12-26",
        "2026-01-01", "2026-04-03", "2026-04-06", "2026-05-04", "2026-05-25",
        "2026-08-31", "2026-12-25", "2026-12-28",
        "2027-01-01", "2027-03-26", "2027-03-29", "2027-05-03", "2027-05-31",
        "2027-08-30", "2027-12-27", "2027-12-28",
    ],
    "scotland": [
        "2024-01-01", "2024-01-02", "2024-03-29", "2024-05-06", "2024-05-27",
        "2024-08-05", "2024-12-02", "2024-12-25", "2024-12-26",
        "2025-01-01", "2025-01-02", "2025-04-18", "2025-05-05", "2025-05-26",
        "2025-08-04", "2025-12-01", "2025-12-25", "2025-12-26",
        "2026-01-01", "2026-01-02", "2026-04-03", "2026-05-04", "2026-05-25",
        "2026-06-15",  # one-off World Cup bank holiday (Scotland only)
        "2026-08-03", "2026-11-30", "2026-12-25", "2026-12-28",
        "2027-01-01", "2027-01-04", "2027-03-26", "2027-05-03", "2027-05-31",
        "2027-08-02", "2027-11-30", "2027-12-27", "2027-12-28",
    ],
    "northern-ireland": [
        "2024-01-01", "2024-03-18", "2024-03-29", "2024-04-01", "2024-05-06",
        "2024-05-27", "2024-07-12", "2024-08-26", "2024-12-25", "2024-12-26",
        "2025-01-01", "2025-03-17", "2025-04-18", "2025-04-21", "2025-05-05",
        "2025-05-26", "2025-07-14", "2025-08-25", "2025-12-25", "2025-12-26",
        "2026-01-01", "2026-03-17", "2026-04-03", "2026-04-06", "2026-05-04",
        "2026-05-25", "2026-07-13", "2026-08-31", "2026-12-25", "2026-12-28",
        "2027-01-01", "2027-03-17", "2027-03-26", "2027-03-29", "2027-05-03",
        "2027-05-31", "2027-07-12", "2027-08-30", "2027-12-27", "2027-12-28",
    ],
}

# (response, resolution) in business hours, keyed by ticket priority.
# QUEUE_TARGETS overrides per queue, e.g. {"InfoSec": {"high": (2, 24)}}.
TARGETS = {
    "urgent": (1, 8),
    "high": (4, 16),
    "medium": (8, 40),
    "low": (16, 80),
}
QUEUE_TARGETS: dict[str, dict[str, tuple[float, float]]] = {}


# ---------- holiday data ----------

_holiday_cache: dict[str, set[date]] | None = None


def _cached_payload() -> dict | None:
    raw = db.all_settings().get(CACHE_KEY)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def holidays(calendar: str = DEFAULT_CALENDAR) -> set[date]:
    """Bank holidays for one calendar — the gov.uk cache if we have it, else the static list."""
    global _holiday_cache
    if _holiday_cache is None:
        payload = _cached_payload() or {}
        dates = payload.get("dates") or {}
        _holiday_cache = {
            key: {date.fromisoformat(d) for d in (dates.get(key) or STATIC_HOLIDAYS[key])}
            for key in CALENDARS
        }
    return _holiday_cache.get(calendar, _holiday_cache[DEFAULT_CALENDAR])


def invalidate() -> None:
    global _holiday_cache
    _holiday_cache = None


def refresh_from_govuk(timeout: float = 15.0) -> dict:
    """Pull the published lists and cache them. Safe to call repeatedly."""
    try:
        resp = httpx.get(GOVUK_URL, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        return {"ok": False, "message": f"Could not reach gov.uk: {str(exc)[:200]}"}

    dates: dict[str, list[str]] = {}
    for key in CALENDARS:
        events = (payload.get(key) or {}).get("events") or []
        found = sorted({e["date"] for e in events if e.get("date")})
        if found:
            dates[key] = found
    if not dates:
        return {"ok": False, "message": "gov.uk returned no usable dates."}

    db.set_setting(CACHE_KEY, json.dumps({"fetched_at": db.now(), "dates": dates}), "gov.uk")
    from . import settings
    settings.invalidate()
    invalidate()
    years = sorted({d[:4] for ds in dates.values() for d in ds})
    return {"ok": True, "message": f"Bank holidays updated from gov.uk — {years[0]}–{years[-1]}.",
            "years": years}


def calendar_status() -> dict:
    """What the clock is currently using, for the Settings dialog."""
    payload = _cached_payload()
    out = {"source": "gov.uk" if payload else "built-in list",
           "fetched_at": (payload or {}).get("fetched_at", ""), "calendars": []}
    for key, spec in CALENDARS.items():
        days = sorted(holidays(key))
        out["calendars"].append({
            "key": key, "label": spec["label"], "count": len(days),
            "from": days[0].isoformat() if days else "", "to": days[-1].isoformat() if days else "",
        })
    return out


# ---------- the clock ----------

def _to_london(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(LONDON)


def _iso_utc(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def is_working_day(day: date, calendar: str = DEFAULT_CALENDAR) -> bool:
    return day.weekday() < 5 and day not in holidays(calendar)


def _day_window(day: date) -> tuple[datetime, datetime]:
    return (datetime.combine(day, BUSINESS_START, LONDON),
            datetime.combine(day, BUSINESS_END, LONDON))


def _next_working_start(moment: datetime, calendar: str) -> datetime:
    """Move a moment forward to the next instant inside business hours."""
    day = moment.date()
    while True:
        if is_working_day(day, calendar):
            start, end = _day_window(day)
            if moment <= start:
                return start
            if moment < end:
                return moment
        day += timedelta(days=1)
        moment = datetime.combine(day, BUSINESS_START, LONDON)


def add_business_hours(start: datetime | str, hours: float, calendar: str = DEFAULT_CALENDAR) -> str:
    """`hours` business hours after `start`, as a UTC ISO string."""
    if hours <= 0:
        # Adding nothing must not move the moment — a deadline sitting exactly at
        # 17:00 would otherwise snap to the next working morning.
        return _iso_utc(_to_london(start))
    cursor = _next_working_start(_to_london(start), calendar)
    remaining = timedelta(hours=max(hours, 0))
    while remaining > timedelta(0):
        _, end = _day_window(cursor.date())
        available = end - cursor
        if remaining <= available:
            return _iso_utc(cursor + remaining)
        remaining -= available
        cursor = _next_working_start(
            datetime.combine(cursor.date() + timedelta(days=1), BUSINESS_START, LONDON), calendar)
    return _iso_utc(cursor)


def business_hours_between(start: datetime | str, end: datetime | str,
                           calendar: str = DEFAULT_CALENDAR) -> float:
    """Business hours elapsed between two moments (0 if they're the wrong way round)."""
    a, b = _to_london(start), _to_london(end)
    if b <= a:
        return 0.0
    total = timedelta(0)
    day = a.date()
    while day <= b.date():
        if is_working_day(day, calendar):
            open_, close = _day_window(day)
            overlap = min(b, close) - max(a, open_)
            if overlap > timedelta(0):
                total += overlap
        day += timedelta(days=1)
    return round(total.total_seconds() / 3600, 3)


def targets_for(priority: str, queue: str = "") -> tuple[float, float]:
    """(response, resolution) business hours for a ticket."""
    per_queue = QUEUE_TARGETS.get(queue, {})
    return per_queue.get(priority) or TARGETS.get(priority) or TARGETS["medium"]


def due_dates(priority: str, queue: str = "", calendar: str = DEFAULT_CALENDAR,
              start: str | None = None) -> tuple[str, str]:
    """Response and resolution due dates for a new ticket."""
    start = start or db.now()
    response_hours, resolution_hours = targets_for(priority, queue)
    return (add_business_hours(start, response_hours, calendar),
            add_business_hours(start, resolution_hours, calendar))
