from __future__ import annotations

import httpx

from app.backend.marketplace import (
    HttpMarketplaceClient,
    MockMarketplaceClient,
    build_marketplace_client,
)
from app.backend.postgres import PostgresMarketplaceClient, PostgresReportingClient
from app.backend.reporting import HttpReportingClient, MockReportingClient, build_reporting_client
from app.settings import Settings


async def _create_campaign(store) -> dict:
    marketplace = MockMarketplaceClient(store)
    return await marketplace.create_campaign(
        {
            "name": "Raporlama Testi",
            "category": "ev-yasam",
            "discount_pct": 10,
            "budget": 20000,
            "starts_at": "2026-09-20",
            "ends_at": "2026-09-30",
            "product_ids": ["P1"],
        }
    )


async def test_mock_reporting_uc_katman(store):
    campaign = await _create_campaign(store)
    reporting = MockReportingClient(store)

    performance = await reporting.campaign_performance(campaign["id"], days=7)
    assert len(performance["series"]) == 7
    assert performance["totals"]["revenue"] > 0

    portfolio = await reporting.portfolio_summary(days=7)
    assert portfolio["active_campaign_count"] == 1
    assert portfolio["totals"]["revenue"] > 0
    assert "anomalies" in portfolio

    products = await reporting.category_product_performance("ev-yasam", days=7)
    assert products["product_count"] > 0
    assert products["totals"]["revenue"] > 0

    empty = await reporting.category_product_performance("yok-boyle-kategori", days=7)
    assert empty["product_count"] == 0


async def test_bilinmeyen_kampanya_hata(store):
    reporting = MockReportingClient(store)
    try:
        await reporting.campaign_performance("CMP-YOK", days=7)
    except ValueError as exc:
        assert "bulunamadi" in str(exc)
    else:
        raise AssertionError("ValueError bekleniyordu")


def test_factory_secimleri(tmp_path):
    store_path = tmp_path / "factory.db"
    store = None  # factory yalnizca settings + store referansi tasir

    default_settings = Settings(database_path=str(store_path))
    assert isinstance(build_marketplace_client(default_settings, store), MockMarketplaceClient)
    assert isinstance(build_reporting_client(default_settings, store), MockReportingClient)

    http_settings = Settings(
        database_path=str(store_path),
        marketplace_api_base_url="http://backoffice.local",
        reporting_api_base_url="http://reporting.local",
    )
    assert isinstance(build_marketplace_client(http_settings, store), HttpMarketplaceClient)
    assert isinstance(build_reporting_client(http_settings, store), HttpReportingClient)

    pg_settings = Settings(database_path=str(store_path), postgres_dsn="postgresql://u:p@localhost:5432/db")
    assert isinstance(build_marketplace_client(pg_settings, store), PostgresMarketplaceClient)
    assert isinstance(build_reporting_client(pg_settings, store), PostgresReportingClient)


def test_postgres_normalizasyon():
    from app.backend.postgres import _normalize_campaign, _normalize_product_ids

    assert _normalize_product_ids(None) == []
    assert _normalize_product_ids("{P1,P2}") == ["P1", "P2"]
    assert _normalize_product_ids(["P1", "P2"]) == ["P1", "P2"]

    campaign = _normalize_campaign(
        {"id": "C1", "product_ids": "{P1}", "discount_pct": 12, "budget": 1000, "starts_at": None}
    )
    assert campaign["product_ids"] == ["P1"]
    assert campaign["discount_pct"] == 12.0

    import datetime

    with_date = _normalize_campaign({"id": "C2", "starts_at": datetime.date(2026, 9, 28)})
    assert with_date["starts_at"] == "2026-09-28"


def test_http_reporting_istemcisi_olusturulur():
    client = HttpReportingClient("http://reporting.local", token="abc")
    assert isinstance(client._client, httpx.AsyncClient)
