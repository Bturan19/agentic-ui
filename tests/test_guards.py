from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.agent.guards import (
    Policy,
    check_budget_change,
    check_campaign_draft,
    load_policy,
    violations_text,
)
from app.db import Store
from app.memory.journal import JournalWriter
from app.memory.learnings import LearningStore
from app.settings import BASE_DIR

ISTANBUL = ZoneInfo("Europe/Istanbul")


@pytest.fixture
def policy() -> Policy:
    return load_policy(BASE_DIR / "config" / "policy.yaml")


@pytest.fixture
async def store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "test.db")
    await store.connect()
    yield store
    await store.close()


@pytest.fixture
def journal(tmp_path: Path) -> JournalWriter:
    return JournalWriter(tmp_path / "journal")


@pytest.fixture
def learnings(tmp_path: Path) -> LearningStore:
    return LearningStore(tmp_path / "memory")


@pytest.fixture
def at_night() -> datetime:
    return datetime(2026, 9, 27, 23, 30, tzinfo=ISTANBUL)


def draft(**overrides) -> dict:
    base = {
        "name": "Test Kampanyasi",
        "category": "ev-yasam",
        "discount_pct": 10,
        "budget": 10000,
        "starts_at": "2026-09-28",
        "ends_at": "2026-09-30",
        "product_ids": ["P1"],
    }
    base.update(overrides)
    return base


class TestCampaignChecks:
    def test_limits_altinda_violation_yok(self, policy):
        assert check_campaign_draft(policy, draft(), active_count=3, now=datetime.now(ISTANBUL)) == []

    def test_indirim_limiti_asilirsa_violation(self, policy):
        violations = check_campaign_draft(policy, draft(discount_pct=30), active_count=0, now=datetime.now(ISTANBUL))
        assert [violation.field for violation in violations] == ["discount_pct"]

    def test_kategori_override_uygulanir(self, policy):
        violations = check_campaign_draft(
            policy, draft(category="elektronik", discount_pct=18), active_count=0, now=datetime.now(ISTANBUL)
        )
        assert [violation.field for violation in violations] == ["discount_pct"]

    def test_butce_limiti(self, policy):
        violations = check_campaign_draft(policy, draft(budget=999999), active_count=0, now=datetime.now(ISTANBUL))
        assert "budget" in [violation.field for violation in violations]

    def test_sure_limiti(self, policy):
        violations = check_campaign_draft(
            policy, draft(starts_at="2026-09-01", ends_at="2026-10-01"), active_count=0, now=datetime.now(ISTANBUL)
        )
        assert "duration_days" in [violation.field for violation in violations]

    def test_aktif_kampanya_limiti(self, policy):
        violations = check_campaign_draft(policy, draft(), active_count=25, now=datetime.now(ISTANBUL))
        assert "active_campaigns" in [violation.field for violation in violations]

    def test_blackout_penceresi(self, policy, at_night):
        policy.blackout.enabled = True
        policy.blackout.windows = ["22:00-07:00"]
        violations = check_campaign_draft(policy, draft(), active_count=0, now=at_night)
        assert "blackout" in [violation.field for violation in violations]

    def test_violations_text_mesaj_uretir(self, policy):
        violations = check_campaign_draft(policy, draft(discount_pct=30), active_count=0, now=datetime.now(ISTANBUL))
        assert "30" in violations_text(violations)


class TestBudgetChecks:
    def test_limit_ici_butce(self, policy):
        campaign = {"id": "CMP-1", "category": "ev-yasam", "budget": 10000}
        assert check_budget_change(policy, campaign, 12000, daily_delta=0, now=datetime.now(ISTANBUL)) == []

    def test_gunluk_delta_limiti(self, policy):
        campaign = {"id": "CMP-1", "category": "ev-yasam", "budget": 10000}
        violations = check_budget_change(
            policy, campaign, 20000, daily_delta=70000, now=datetime.now(ISTANBUL)
        )
        assert "daily_budget_delta" in [violation.field for violation in violations]


class TestLearnings:
    def test_kaydeder_ve_tekrari_engeller(self, learnings: LearningStore):
        assert learnings.append("ROAS dusukken butce artirmak ise yaramiyor", tags=["roas"]) == "kaydedildi"
        assert learnings.append("ROAS dusukken butce artirmak ise yaramiyor", tags=["roas"]) == "zaten kayitli"
        assert "ROAS" in learnings.read()


class TestJournal:
    def test_gunluge_yazar(self, journal: JournalWriter):
        path = journal.append(
            title="Test",
            summary="Ozet",
            actions=["Aksiyon 1"],
            learnings=["Ogrenim 1"],
            tags=["test"],
            conversation_id="conv_1",
        )
        assert Path(path).exists()
        content = journal.read_day(journal.list_days()[0])
        assert "Aksiyon 1" in content and "Ogrenim 1" in content
