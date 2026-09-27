from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic_ai import Agent, ApprovalRequired, RunContext

from . import guards
from .deps import AgentDeps


def _now(deps: AgentDeps) -> datetime:
    return datetime.now(ZoneInfo(deps.settings.timezone))


def _normalize_args(args: Any) -> dict:
    if args is None:
        return {}
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
            return parsed if isinstance(parsed, dict) else {"args": parsed}
        except json.JSONDecodeError:
            return {"raw": args}
    return args


def _preview(value: Any, limit: int = 1500) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + f"… ({len(text)} karakter)"


def register_campaign_tools(agent: Agent[AgentDeps]) -> None:
    @agent.tool
    async def list_campaigns(ctx: RunContext[AgentDeps], status: str = "active") -> list[dict]:
        """Pazaryeri kampanyalarini listeler. status: active | paused | draft | all"""
        return await ctx.deps.marketplace.list_campaigns(status)

    @agent.tool
    async def get_campaign(ctx: RunContext[AgentDeps], campaign_id: str) -> dict | str:
        """Tek bir kampanyanin detayini dondurur."""
        campaign = await ctx.deps.marketplace.get_campaign(campaign_id)
        return campaign or f"Kampanya bulunamadi: {campaign_id}"

    @agent.tool
    async def get_campaign_performance(
        ctx: RunContext[AgentDeps], campaign_id: str, days: int = 7
    ) -> dict:
        """Kampanyanin gun bazinda performansi: ciro, harcama, siparis, marj, ROAS."""
        return await ctx.deps.reporting.campaign_performance(campaign_id, max(1, min(days, 30)))

    @agent.tool
    async def portfolio_summary(ctx: RunContext[AgentDeps], days: int = 7) -> dict:
        """Tum aktif portfoyun ozeti: toplam ciro/marj, en iyi/kotu kampanyalar, anormallikler."""
        return await ctx.deps.reporting.portfolio_summary(max(1, min(days, 30)))

    @agent.tool
    async def category_product_performance(
        ctx: RunContext[AgentDeps], category: str, days: int = 7, min_revenue: float = 0.0
    ) -> dict:
        """Bir kategoride urun bazli ciro/marj/ROAS performansi (kampanya urun secimi icin)."""
        return await ctx.deps.reporting.category_product_performance(
            category, max(1, min(days, 30)), min_revenue
        )

    @agent.tool
    async def propose_campaign(
        ctx: RunContext[AgentDeps],
        name: str,
        category: str,
        discount_pct: float,
        budget: float,
        starts_at: str,
        ends_at: str,
        product_ids: list[str],
        rationale: str,
    ) -> dict:
        """Yeni kampanya olusturur (YAZMA islemi).

        Politika limitleri asilirsa sistem kullanici onayini ister ve aksiyon
        duraklatilir; onay sonrasi ayni cagri kaldigi yerden calisir. Onay
        gerektirmeyen (limit icindeki) kampanyalar dogrudan olusturulur.
        starts_at/ends_at ISO tarih: YYYY-MM-DD.
        """
        draft = {
            "name": name,
            "category": category,
            "discount_pct": discount_pct,
            "budget": budget,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "product_ids": product_ids,
            "notes": f"Oneri gerekcesi: {rationale}",
        }
        active_count = await ctx.deps.marketplace.count_active()
        violations = guards.check_campaign_draft(
            ctx.deps.policy, draft, active_count=active_count, now=_now(ctx.deps)
        )
        if violations and not ctx.tool_call_approved:
            raise ApprovalRequired(
                metadata={
                    "reason": guards.violations_text(violations),
                    "violations": [violation.message for violation in violations],
                }
            )
        created = await ctx.deps.marketplace.create_campaign(draft)
        return {
            "status": "created",
            "campaign": created,
            "approval_required": bool(violations),
            "rationale": rationale,
        }

    @agent.tool
    async def update_campaign_budget(
        ctx: RunContext[AgentDeps], campaign_id: str, new_budget: float, rationale: str
    ) -> dict:
        """Kampanya butcesini gunceller (YAZMA islemi). Limit asilirsa onay ister."""
        campaign = await ctx.deps.marketplace.get_campaign(campaign_id)
        if campaign is None:
            return f"Kampanya bulunamadi: {campaign_id}"
        daily_delta = await ctx.deps.store.budget_delta_since(
            _now(ctx.deps).replace(hour=0, minute=0, second=0).isoformat()
        )
        violations = guards.check_budget_change(
            ctx.deps.policy, campaign, new_budget, daily_delta=daily_delta, now=_now(ctx.deps)
        )
        if violations and not ctx.tool_call_approved:
            raise ApprovalRequired(
                metadata={
                    "reason": guards.violations_text(violations),
                    "violations": [violation.message for violation in violations],
                }
            )
        old_budget = float(campaign.get("budget") or 0)
        updated = await ctx.deps.marketplace.update_budget(campaign_id, new_budget, note=rationale)
        await ctx.deps.store.audit(
            "budget_change",
            conversation_id=ctx.deps.conversation_id,
            run_id=ctx.deps.run_id,
            name=campaign_id,
            payload={"delta": new_budget - old_budget, "old": old_budget, "new": new_budget, "rationale": rationale},
        )
        return {"status": "updated", "campaign": updated, "approval_required": bool(violations)}

    @agent.tool
    async def pause_campaign(
        ctx: RunContext[AgentDeps], campaign_id: str, rationale: str
    ) -> dict:
        """Kampanyayi durdurur (guvenli yon; onay gerektirmez)."""
        try:
            campaign = await ctx.deps.marketplace.set_status(campaign_id, "paused", note=rationale)
        except ValueError as exc:
            return str(exc)
        return {"status": "paused", "campaign": campaign}

    @agent.tool
    async def resume_campaign(
        ctx: RunContext[AgentDeps], campaign_id: str, rationale: str
    ) -> dict:
        """Durdurulmus kampanyayi yeniden baslatir (YAZMA islemi). Aktif kampanya limiti asilirsa onay ister."""
        campaign = await ctx.deps.marketplace.get_campaign(campaign_id)
        if campaign is None:
            return f"Kampanya bulunamadi: {campaign_id}"
        active_count = await ctx.deps.marketplace.count_active()
        violations = []
        if active_count >= ctx.deps.policy.limits.max_active_campaigns:
            violations.append(
                f"Aktif kampanya limiti ({ctx.deps.policy.limits.max_active_campaigns}) dolu"
            )
        if guards.is_blackout(ctx.deps.policy, _now(ctx.deps)):
            violations.append("Blackout penceresi aktif")
        if violations and not ctx.tool_call_approved:
            raise ApprovalRequired(metadata={"reason": " | ".join(violations), "violations": violations})
        updated = await ctx.deps.marketplace.set_status(campaign_id, "active", note=rationale)
        return {"status": "active", "campaign": updated, "approval_required": bool(violations)}


def register_memory_tools(agent: Agent[AgentDeps]) -> None:
    @agent.tool
    async def save_learning(
        ctx: RunContext[AgentDeps], text: str, tags: list[str] | None = None
    ) -> str:
        """Kalici ogrenim kaydeder (sonraki calismalarda otomatik okunur)."""
        return ctx.deps.learnings.append(text, tags=tags, source=ctx.deps.conversation_id)

    @agent.tool
    async def write_journal(
        ctx: RunContext[AgentDeps],
        title: str,
        summary: str,
        actions: list[str] | None = None,
        learnings: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """Bu calismanin ozetini, aksiyonlarini ve ogrenimlerini gunluge yazar."""
        path = ctx.deps.journal.append(
            title=title,
            summary=summary,
            actions=actions,
            learnings=learnings,
            tags=tags,
            conversation_id=ctx.deps.conversation_id,
        )
        return f"Journal kaydedildi: {path}"

    @agent.tool
    async def read_journal(ctx: RunContext[AgentDeps], days: int = 7) -> str:
        """Son gunlerin journal kayitlarini okur."""
        text = ctx.deps.journal.read_recent(days=max(1, min(days, 30)))
        return text or "(journal kaydi yok)"

    @agent.tool
    async def get_recent_audit(ctx: RunContext[AgentDeps], days: int = 7) -> list[dict]:
        """Son gunlerin aksiyon kayitlarini (audit trail) dondurur."""
        entries = await ctx.deps.store.list_audit(days=max(1, min(days, 30)), limit=200)
        return [
            {"ts": entry["ts"], "kind": entry["kind"], "name": entry["name"],
             "payload": _preview(entry.get("payload_json") or "", 400)}
            for entry in entries
        ]

    @agent.tool
    def list_skills(ctx: RunContext[AgentDeps]) -> str:
        """Kullanilabilir skill'leri listeler."""
        return ctx.deps.skills.index_markdown()

    @agent.tool
    def read_skill(ctx: RunContext[AgentDeps], name: str) -> str:
        """Bir skill'in tam icerigini okur (prosedur detaylari icin)."""
        skill = ctx.deps.skills.read(name)
        if skill is None:
            return f"Skill bulunamadi: {name}"
        return skill.body

    @agent.tool
    def get_policy(ctx: RunContext[AgentDeps]) -> str:
        """Gecerli kampanya politikasini (limitler, ekonomi hedefleri) dondurur."""
        path = ctx.deps.settings.policy_file
        if path.exists():
            return path.read_text(encoding="utf-8")
        return guards.policy_summary(ctx.deps.policy)
