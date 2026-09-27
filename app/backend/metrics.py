from __future__ import annotations

import hashlib
import random
from datetime import UTC, date, datetime, timedelta


def daily_metrics(campaign: dict, day: date) -> dict:
    """Kampanya + gun bazinda deterministik, makul gorunumlu metrikler uretir."""
    seed = hashlib.md5(f"{campaign['id']}:{day.isoformat()}".encode()).hexdigest()
    rng = random.Random(int(seed[:12], 16))
    starts, ends = campaign.get("starts_at"), campaign.get("ends_at")
    active = True
    if starts and day.isoformat() < str(starts)[:10]:
        active = False
    if ends and day.isoformat() > str(ends)[:10]:
        active = False
    if not active or campaign.get("status") != "active":
        return {
            "date": day.isoformat(),
            "impressions": 0,
            "clicks": 0,
            "orders": 0,
            "revenue": 0.0,
            "spend": 0.0,
            "margin": 0.0,
        }

    discount = float(campaign.get("discount_pct") or 0)
    budget = float(campaign.get("budget") or 0)
    if starts and ends:
        span = (date.fromisoformat(str(ends)[:10]) - date.fromisoformat(str(starts)[:10])).days + 1
        daily_budget = budget / max(1, span)
    else:
        daily_budget = budget / 7
    impressions = int(daily_budget * 18 * rng.uniform(0.75, 1.35))
    clicks = int(impressions * rng.uniform(0.012, 0.032))
    orders = int(clicks * rng.uniform(0.02, 0.065) * (1 + discount / 200))
    basket = rng.uniform(180, 850)
    revenue = round(orders * basket, 2)
    spend = round(min(daily_budget, revenue / rng.uniform(0.8, 1.6)) if revenue else daily_budget * 0.3, 2)
    margin_pct = max(0.02, rng.uniform(0.08, 0.24) - discount / 400)
    margin = round(revenue * margin_pct, 2)
    return {
        "date": day.isoformat(),
        "impressions": impressions,
        "clicks": clicks,
        "orders": orders,
        "revenue": revenue,
        "spend": spend,
        "margin": margin,
    }


def generate_series(campaign: dict, days: int, today: date | None = None) -> list[dict]:
    today = today or datetime.now(UTC).date()
    return [daily_metrics(campaign, today - timedelta(days=offset)) for offset in range(days - 1, -1, -1)]


def aggregate(series: list[dict]) -> dict:
    totals = {
        key: round(sum(item[key] for item in series), 2)
        for key in ("impressions", "clicks", "orders", "revenue", "spend", "margin")
    }
    totals["roas"] = round(totals["revenue"] / totals["spend"], 2) if totals["spend"] else 0.0
    totals["margin_pct"] = (
        round(100 * totals["margin"] / totals["revenue"], 1) if totals["revenue"] else 0.0
    )
    return totals
