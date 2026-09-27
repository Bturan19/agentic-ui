from __future__ import annotations

import re
import uuid
from typing import Any

from ..settings import Settings
from .metrics import aggregate
from .reporting import build_portfolio_summary, combine_totals

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def _table(name: str) -> str:
    if not _IDENTIFIER.match(name):
        raise ValueError(f"Gecersiz tablo adi: {name}")
    return name


def _normalize_product_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith("{") and raw.endswith("}") or raw.startswith("[") and raw.endswith("]"):
            raw = raw[1:-1]
        return [item.strip().strip('"').strip("'") for item in raw.split(",") if item.strip()]
    return [str(item) for item in value]


def _normalize_campaign(row: dict) -> dict:
    campaign = dict(row)
    for key in ("starts_at", "ends_at", "created_at", "updated_at"):
        value = campaign.get(key)
        if value is not None and not isinstance(value, str):
            campaign[key] = value.isoformat()
    campaign["product_ids"] = _normalize_product_ids(campaign.get("product_ids"))
    for key in ("discount_pct", "budget"):
        if campaign.get(key) is not None:
            campaign[key] = float(campaign[key])
    return campaign


def _normalize_metrics(row: dict) -> dict:
    return {
        "date": row["date"].isoformat() if not isinstance(row["date"], str) else row["date"],
        "impressions": int(row.get("impressions") or 0),
        "clicks": int(row.get("clicks") or 0),
        "orders": int(row.get("orders") or 0),
        "revenue": float(row.get("revenue") or 0),
        "spend": float(row.get("spend") or 0),
        "margin": float(row.get("margin") or 0),
    }


class _Pool:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool = None

    async def _connect(self):
        if self._pool is None:
            try:
                import asyncpg
            except ImportError as exc:  # pragma: no cover - bagimlilik hatasi
                raise RuntimeError(
                    "Postgres icin asyncpg gerekir: uv sync --extra postgres"
                ) from exc
            self._pool = await asyncpg.create_pool(
                self._dsn, min_size=1, max_size=5, command_timeout=60
            )
        return self._pool

    async def fetch(self, query: str, *args: Any) -> list[dict]:
        pool = await self._connect()
        async with pool.acquire() as connection:
            return [dict(record) for record in await connection.fetch(query, *args)]

    async def fetchrow(self, query: str, *args: Any) -> dict | None:
        rows = await self.fetch(query, *args)
        return rows[0] if rows else None

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


class PostgresMarketplaceClient:
    """Pazaryeri kampanya tablolari icin PostgreSQL adaptoru.

    Beklenen tablo/kolonlar (tablo adlari .env ile degistirilebilir):
      campaigns(id, name, category, status, discount_pct, budget,
                starts_at, ends_at, product_ids text[], notes, created_at, updated_at)
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool = _Pool(settings.postgres_dsn)

    @property
    def _campaigns(self) -> str:
        return _table(self._settings.pg_campaigns_table)

    async def aclose(self) -> None:
        await self._pool.close()

    async def list_campaigns(self, status: str | None = None) -> list[dict]:
        if status and status != "all":
            rows = await self._pool.fetch(
                f"SELECT * FROM {self._campaigns} WHERE status = $1 ORDER BY updated_at DESC NULLS LAST",
                status,
            )
        else:
            rows = await self._pool.fetch(
                f"SELECT * FROM {self._campaigns} ORDER BY updated_at DESC NULLS LAST"
            )
        return [_normalize_campaign(row) for row in rows]

    async def get_campaign(self, campaign_id: str) -> dict | None:
        row = await self._pool.fetchrow(f"SELECT * FROM {self._campaigns} WHERE id = $1", campaign_id)
        return _normalize_campaign(row) if row else None

    async def count_active(self) -> int:
        rows = await self._pool.fetch(
            f"SELECT COUNT(*) AS n FROM {self._campaigns} WHERE status = 'active'"
        )
        return int(rows[0]["n"]) if rows else 0

    async def create_campaign(self, draft: dict) -> dict:
        campaign_id = f"CMP-{uuid.uuid4().hex[:8].upper()}"
        row = await self._pool.fetchrow(
            f"""
            INSERT INTO {self._campaigns}
                (id, name, category, status, discount_pct, budget, starts_at, ends_at,
                 product_ids, notes, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW(), NOW())
            RETURNING *
            """,
            campaign_id,
            draft.get("name"),
            draft.get("category") or "",
            draft.get("status") or "active",
            float(draft.get("discount_pct") or 0),
            float(draft.get("budget") or 0),
            draft.get("starts_at"),
            draft.get("ends_at"),
            list(draft.get("product_ids") or []),
            draft.get("notes"),
        )
        assert row is not None
        return _normalize_campaign(row)

    async def update_budget(self, campaign_id: str, budget: float, note: str) -> dict:
        row = await self._pool.fetchrow(
            f"""
            UPDATE {self._campaigns} SET budget = $2, notes = $3, updated_at = NOW()
            WHERE id = $1 RETURNING *
            """,
            campaign_id,
            float(budget),
            note,
        )
        if row is None:
            raise ValueError(f"Kampanya bulunamadi: {campaign_id}")
        return _normalize_campaign(row)

    async def set_status(self, campaign_id: str, status: str, note: str) -> dict:
        row = await self._pool.fetchrow(
            f"""
            UPDATE {self._campaigns} SET status = $2, notes = $3, updated_at = NOW()
            WHERE id = $1 RETURNING *
            """,
            campaign_id,
            status,
            note,
        )
        if row is None:
            raise ValueError(f"Kampanya bulunamadi: {campaign_id}")
        return _normalize_campaign(row)


class PostgresReportingClient:
    """Raporlama icin PostgreSQL adaptoru.

    Beklenen tablolar (adlar .env ile degistirilebilir):
      campaign_daily(campaign_id, date, revenue, orders, spend, margin, [impressions, clicks])
      category_product_daily(category, product_id, product_name, date, revenue, orders, margin, spend)
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool = _Pool(settings.postgres_dsn)

    async def aclose(self) -> None:
        await self._pool.close()

    @property
    def _campaigns(self) -> str:
        return _table(self._settings.pg_campaigns_table)

    @property
    def _daily(self) -> str:
        return _table(self._settings.pg_campaign_daily_table)

    @property
    def _category_daily(self) -> str:
        return _table(self._settings.pg_category_daily_table)

    async def campaign_performance(self, campaign_id: str, days: int) -> dict:
        rows = await self._pool.fetch(
            f"""
            SELECT date, SUM(revenue) AS revenue, SUM(orders) AS orders,
                   SUM(spend) AS spend, SUM(margin) AS margin
            FROM {self._daily}
            WHERE campaign_id = $1 AND date >= CURRENT_DATE - ($2::int - 1)
            GROUP BY date ORDER BY date
            """,
            campaign_id,
            days,
        )
        series = [_normalize_metrics(row) for row in rows]
        campaign = await self.get_campaign(campaign_id)
        return {
            "campaign": campaign or {"id": campaign_id},
            "days": days,
            "series": series,
            "totals": aggregate(series) if series else combine_totals([]),
        }

    async def get_campaign(self, campaign_id: str) -> dict | None:
        row = await self._pool.fetchrow(f"SELECT * FROM {self._campaigns} WHERE id = $1", campaign_id)
        return _normalize_campaign(row) if row else None

    async def portfolio_summary(self, days: int) -> dict:
        rows = await self._pool.fetch(
            f"""
            SELECT c.*, SUM(d.revenue) AS revenue, SUM(d.orders) AS orders,
                   SUM(d.spend) AS spend, SUM(d.margin) AS margin
            FROM {self._campaigns} c
            JOIN {self._daily} d ON d.campaign_id = c.id
            WHERE d.date >= CURRENT_DATE - ($1::int - 1) AND c.status = 'active'
            GROUP BY c.id
            """,
            days,
        )
        per_campaign = [
            {"campaign": _normalize_campaign(row), "totals": _totals_from_row(row)} for row in rows
        ]

        yesterday_rows = await self._pool.fetch(
            f"SELECT SUM(revenue) AS revenue FROM {self._daily} WHERE date = CURRENT_DATE - 1"
        )
        yesterday = float(yesterday_rows[0]["revenue"] or 0) if yesterday_rows else 0.0

        combined = combine_totals([entry["totals"] for entry in per_campaign])
        return build_portfolio_summary(
            days=days,
            per_campaign=per_campaign,
            yesterday_revenue=yesterday,
            previous_avg_revenue=combined["revenue"] / max(1, days),
        )

    async def category_product_performance(
        self, category: str, days: int, min_revenue: float = 0.0
    ) -> dict:
        rows = await self._pool.fetch(
            f"""
            SELECT product_id, product_name, SUM(revenue) AS revenue, SUM(orders) AS orders,
                   SUM(margin) AS margin, SUM(spend) AS spend
            FROM {self._category_daily}
            WHERE category = $1 AND date >= CURRENT_DATE - ($2::int - 1)
            GROUP BY product_id, product_name
            HAVING SUM(revenue) >= $3
            ORDER BY revenue DESC
            LIMIT 100
            """,
            category,
            days,
            min_revenue,
        )
        products = []
        for row in rows:
            revenue = float(row.get("revenue") or 0)
            spend = float(row.get("spend") or 0)
            margin = float(row.get("margin") or 0)
            products.append(
                {
                    "product_id": row.get("product_id"),
                    "product_name": row.get("product_name"),
                    "revenue": round(revenue, 2),
                    "orders": int(row.get("orders") or 0),
                    "margin": round(margin, 2),
                    "margin_pct": round(100 * margin / revenue, 1) if revenue else 0.0,
                    "spend": round(spend, 2),
                    "roas": round(revenue / spend, 2) if spend else 0.0,
                }
            )
        totals = combine_totals(
            [{"revenue": product["revenue"], "margin": product["margin"], "spend": product["spend"]}
             for product in products]
        )
        return {
            "category": category,
            "days": days,
            "product_count": len(products),
            "products": products,
            "totals": totals,
        }


def _totals_from_row(row: dict) -> dict:
    return combine_totals(
        [
            {
                "revenue": row.get("revenue") or 0,
                "orders": row.get("orders") or 0,
                "spend": row.get("spend") or 0,
                "margin": row.get("margin") or 0,
            }
        ]
    )
