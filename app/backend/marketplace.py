from __future__ import annotations

import uuid
from typing import Protocol

import httpx

from ..db import Store
from ..settings import Settings


class MarketplaceClient(Protocol):
    """Pazaryeri back office' i ile konusan yazma/okuma arayuzu.

    Not: performans/raporlama verisi ayridir; bkz. `ReportingClient` (reporting.py).
    """

    async def list_campaigns(self, status: str | None = None) -> list[dict]: ...
    async def get_campaign(self, campaign_id: str) -> dict | None: ...
    async def count_active(self) -> int: ...
    async def create_campaign(self, draft: dict) -> dict: ...
    async def update_budget(self, campaign_id: str, budget: float, note: str) -> dict: ...
    async def set_status(self, campaign_id: str, status: str, note: str) -> dict: ...


class MockMarketplaceClient:
    """SQLite uzerinde calisan sahte pazaryeri backend'i (gelistirme icin)."""

    def __init__(self, store: Store) -> None:
        self._store = store

    async def list_campaigns(self, status: str | None = None) -> list[dict]:
        return await self._store.list_campaigns(status)

    async def get_campaign(self, campaign_id: str) -> dict | None:
        return await self._store.get_campaign(campaign_id)

    async def count_active(self) -> int:
        return await self._store.count_active_campaigns()

    async def create_campaign(self, draft: dict) -> dict:
        campaign = {
            "id": f"CMP-{uuid.uuid4().hex[:8].upper()}",
            "name": draft["name"],
            "category": draft.get("category") or "",
            "status": draft.get("status") or "active",
            "discount_pct": float(draft.get("discount_pct") or 0),
            "budget": float(draft.get("budget") or 0),
            "starts_at": draft.get("starts_at"),
            "ends_at": draft.get("ends_at"),
            "product_ids": draft.get("product_ids") or [],
            "notes": draft.get("notes"),
        }
        await self._store.upsert_campaign(campaign)
        return await self._store.get_campaign(campaign["id"]) or campaign

    async def update_budget(self, campaign_id: str, budget: float, note: str) -> dict:
        campaign = await self._store.get_campaign(campaign_id)
        if campaign is None:
            raise ValueError(f"Kampanya bulunamadi: {campaign_id}")
        campaign["budget"] = float(budget)
        campaign["notes"] = note
        await self._store.upsert_campaign(campaign)
        return campaign

    async def set_status(self, campaign_id: str, status: str, note: str) -> dict:
        campaign = await self._store.get_campaign(campaign_id)
        if campaign is None:
            raise ValueError(f"Kampanya bulunamadi: {campaign_id}")
        campaign["status"] = status
        campaign["notes"] = note
        await self._store.upsert_campaign(campaign)
        return campaign


class HttpMarketplaceClient:
    """Gercek pazaryeri back office API'si icin HTTP istemcisi.

    Endpoint sozlesmesi ornek olarak verilmistir; kendi API'nize gore uyarlayin
    (sozlesme: docs/integration.md). `marketplace_dry_run=True` iken tum yazma
    cagrilari `dry_run=true` parametresi ile gonderilir.
    """

    def __init__(self, base_url: str, token: str = "", dry_run: bool = True) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.AsyncClient(base_url=base_url, headers=headers, timeout=30)
        self._dry_run = dry_run

    async def aclose(self) -> None:
        await self._client.aclose()

    def _write_params(self) -> dict:
        return {"dry_run": "true"} if self._dry_run else {}

    async def list_campaigns(self, status: str | None = None) -> list[dict]:
        response = await self._client.get("/campaigns", params={"status": status} if status else None)
        response.raise_for_status()
        return response.json()

    async def get_campaign(self, campaign_id: str) -> dict | None:
        response = await self._client.get(f"/campaigns/{campaign_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def count_active(self) -> int:
        response = await self._client.get(
            "/campaigns", params={"status": "active", "count_only": "true"}
        )
        response.raise_for_status()
        body = response.json()
        if isinstance(body, dict):
            return int(body.get("count", 0))
        return len(body)

    async def create_campaign(self, draft: dict) -> dict:
        response = await self._client.post("/campaigns", json=draft, params=self._write_params())
        response.raise_for_status()
        return response.json()

    async def update_budget(self, campaign_id: str, budget: float, note: str) -> dict:
        response = await self._client.patch(
            f"/campaigns/{campaign_id}",
            json={"budget": budget, "note": note},
            params=self._write_params(),
        )
        response.raise_for_status()
        return response.json()

    async def set_status(self, campaign_id: str, status: str, note: str) -> dict:
        response = await self._client.post(
            f"/campaigns/{campaign_id}/status",
            json={"status": status, "note": note},
            params=self._write_params(),
        )
        response.raise_for_status()
        return response.json()


def build_marketplace_client(settings: Settings, store: Store) -> MarketplaceClient:
    if settings.marketplace_api_base_url:
        return HttpMarketplaceClient(
            settings.marketplace_api_base_url,
            settings.marketplace_api_token,
            settings.marketplace_dry_run,
        )
    if settings.postgres_dsn:
        from .postgres import PostgresMarketplaceClient

        return PostgresMarketplaceClient(settings)
    return MockMarketplaceClient(store)
