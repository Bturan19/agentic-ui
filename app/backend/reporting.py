from __future__ import annotations

import hashlib
import random
from datetime import UTC, datetime
from typing import Protocol

import httpx

from ..db import Store, now_iso
from ..settings import Settings
from .metrics import aggregate, generate_series


class ReportingClient(Protocol):
    """Raporlama/analitik verisi saglayicisi.

    Gercek hayatta bu katman BigQuery (MCP Toolbox), sirket ici raporlama API'si
    veya PostgreSQL olabilir. Ajan tool'lari bu arayuzu kullanir.
    """

    async def campaign_performance(self, campaign_id: str, days: int) -> dict: ...
    async def portfolio_summary(self, days: int) -> dict: ...
    async def category_product_performance(
        self, category: str, days: int, min_revenue: float = 0.0
    ) -> dict: ...


def combine_totals(totals_list: list[dict]) -> dict:
    keys = ("impressions", "clicks", "orders", "revenue", "spend", "margin")
    combined = {key: round(sum(float(item.get(key) or 0) for item in totals_list), 2) for key in keys}
    combined["roas"] = round(combined["revenue"] / combined["spend"], 2) if combined["spend"] else 0.0
    combined["margin_pct"] = (
        round(100 * combined["margin"] / combined["revenue"], 1) if combined["revenue"] else 0.0
    )
    return combined


def build_portfolio_summary(
    *,
    days: int,
    per_campaign: list[dict],
    yesterday_revenue: float = 0.0,
    previous_avg_revenue: float = 0.0,
) -> dict:
    anomalies = []
    for entry in per_campaign:
        totals = entry["totals"]
        if totals["roas"] and totals["roas"] < 1:
            anomalies.append(
                {
                    "campaign_id": entry["campaign"]["id"],
                    "name": entry["campaign"].get("name"),
                    "issue": "ROAS 1'in altinda",
                    "roas": totals["roas"],
                }
            )
        if totals["margin"] and totals["margin_pct"] < 5:
            anomalies.append(
                {
                    "campaign_id": entry["campaign"]["id"],
                    "name": entry["campaign"].get("name"),
                    "issue": "Marj %5'in altinda",
                    "margin_pct": totals["margin_pct"],
                }
            )
    return {
        "days": days,
        "generated_at": now_iso(),
        "active_campaign_count": len(per_campaign),
        "totals": combine_totals([entry["totals"] for entry in per_campaign]),
        "yesterday_revenue": round(yesterday_revenue, 2),
        "previous_avg_revenue": round(previous_avg_revenue, 2),
        "top": sorted(per_campaign, key=lambda entry: entry["totals"]["revenue"], reverse=True)[:5],
        "bottom": sorted(per_campaign, key=lambda entry: entry["totals"]["revenue"])[:5],
        "anomalies": anomalies,
    }


class MockReportingClient:
    """SQLite'daki mock kampanyalardan deterministik rapor uretir (gelistirme)."""

    def __init__(self, store: Store) -> None:
        self._store = store

    async def campaign_performance(self, campaign_id: str, days: int) -> dict:
        campaign = await self._store.get_campaign(campaign_id)
        if campaign is None:
            raise ValueError(f"Kampanya bulunamadi: {campaign_id}")
        series = generate_series(campaign, days)
        return {"campaign": campaign, "days": days, "series": series, "totals": aggregate(series)}

    async def portfolio_summary(self, days: int) -> dict:
        campaigns = await self._store.list_campaigns()
        today = datetime.now(UTC).date()
        active = [campaign for campaign in campaigns if campaign.get("status") == "active"]
        per_campaign = [
            {"campaign": campaign, "totals": aggregate(generate_series(campaign, days, today))}
            for campaign in active
        ]
        yesterday_revenue = sum(
            aggregate(generate_series(campaign, 1, today))["revenue"] for campaign in campaigns
        )
        daily_revenues = [
            aggregate(generate_series(campaign, days, today))["revenue"] / max(1, days)
            for campaign in campaigns
        ]
        previous_avg = sum(daily_revenues) / len(daily_revenues) if daily_revenues else 0.0
        return build_portfolio_summary(
            days=days,
            per_campaign=per_campaign,
            yesterday_revenue=yesterday_revenue,
            previous_avg_revenue=previous_avg,
        )

    async def category_product_performance(
        self, category: str, days: int, min_revenue: float = 0.0
    ) -> dict:
        campaigns = [
            campaign
            for campaign in await self._store.list_campaigns()
            if campaign.get("category") == category
        ]
        products: list[dict] = []
        for campaign in campaigns:
            seed = int(hashlib.md5(f"{campaign['id']}:{category}".encode()).hexdigest()[:8], 16)
            rng = random.Random(seed)
            for index in range(rng.randint(2, 5)):
                revenue = round(rng.uniform(5000, 90000), 2)
                orders = max(1, int(revenue / rng.uniform(150, 600)))
                margin_pct = rng.uniform(0.05, 0.28)
                spend = round(revenue / rng.uniform(0.9, 2.2), 2)
                products.append(
                    {
                        "campaign_id": campaign["id"],
                        "product_id": f"P-{campaign['id'][-4:]}{index + 1}",
                        "product_name": f"{category} urun {index + 1}",
                        "revenue": revenue,
                        "orders": orders,
                        "margin": round(revenue * margin_pct, 2),
                        "margin_pct": round(margin_pct * 100, 1),
                        "spend": spend,
                        "roas": round(revenue / spend, 2) if spend else 0.0,
                    }
                )

        filtered = [product for product in products if product["revenue"] >= min_revenue]
        filtered.sort(key=lambda product: product["revenue"], reverse=True)
        total_revenue = round(sum(product["revenue"] for product in filtered), 2)
        total_margin = round(sum(product["margin"] for product in filtered), 2)
        total_spend = round(sum(product["spend"] for product in filtered), 2)
        return {
            "category": category,
            "days": days,
            "product_count": len(filtered),
            "products": filtered[:100],
            "totals": {
                "revenue": total_revenue,
                "margin": total_margin,
                "spend": total_spend,
                "margin_pct": round(100 * total_margin / total_revenue, 1) if total_revenue else 0.0,
                "roas": round(total_revenue / total_spend, 2) if total_spend else 0.0,
            },
        }


class HttpReportingClient:
    """Sirket ici raporlama servisi icin HTTP istemcisi.

    Beklenen endpoint'ler (sozlesme: docs/integration.md):
      GET /reporting/campaign-performance?campaign_id=&days=
      GET /reporting/portfolio-summary?days=
      GET /reporting/category-product-performance?category=&days=&min_revenue=
    """

    def __init__(self, base_url: str, token: str = "") -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.AsyncClient(base_url=base_url, headers=headers, timeout=60)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def campaign_performance(self, campaign_id: str, days: int) -> dict:
        response = await self._client.get(
            "/reporting/campaign-performance", params={"campaign_id": campaign_id, "days": days}
        )
        response.raise_for_status()
        return response.json()

    async def portfolio_summary(self, days: int) -> dict:
        response = await self._client.get("/reporting/portfolio-summary", params={"days": days})
        response.raise_for_status()
        return response.json()

    async def category_product_performance(
        self, category: str, days: int, min_revenue: float = 0.0
    ) -> dict:
        response = await self._client.get(
            "/reporting/category-product-performance",
            params={"category": category, "days": days, "min_revenue": min_revenue},
        )
        response.raise_for_status()
        return response.json()


def build_reporting_client(settings: Settings, store: Store) -> ReportingClient:
    if settings.reporting_api_base_url:
        return HttpReportingClient(settings.reporting_api_base_url, settings.reporting_api_token)
    if settings.postgres_dsn:
        from .postgres import PostgresReportingClient

        return PostgresReportingClient(settings)
    return MockReportingClient(store)
