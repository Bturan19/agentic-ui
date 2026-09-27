from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class AutonomousLimits(BaseModel):
    max_discount_pct: float = 20
    max_budget_per_campaign: float = 50000
    max_daily_budget_delta: float = 75000
    max_active_campaigns: int = 25
    max_campaign_days: int = 14


class CategoryLimit(BaseModel):
    max_discount_pct: float | None = None
    max_budget_per_campaign: float | None = None


class BlackoutRule(BaseModel):
    enabled: bool = False
    windows: list[str] = Field(default_factory=list)


class Economics(BaseModel):
    min_expected_margin_pct: float = 8
    target_roi: float = 1.3


class Policy(BaseModel):
    currency: str = "TRY"
    limits: AutonomousLimits = AutonomousLimits()
    categories: dict[str, CategoryLimit] = Field(default_factory=dict)
    blackout: BlackoutRule = BlackoutRule()
    economics: Economics = Economics()


@dataclass
class Violation:
    field: str
    value: Any
    limit: Any
    message: str


def load_policy(path: Path) -> Policy:
    if not path.exists():
        return Policy()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Policy.model_validate(data)


def effective_limits(policy: Policy, category: str | None) -> tuple[float, float]:
    """Kategori bazli override'lari uygulayarak (max_discount_pct, max_budget) dondurur."""
    discount = policy.limits.max_discount_pct
    budget = policy.limits.max_budget_per_campaign
    if category:
        override = policy.categories.get(category)
        if override:
            if override.max_discount_pct is not None:
                discount = override.max_discount_pct
            if override.max_budget_per_campaign is not None:
                budget = override.max_budget_per_campaign
    return discount, budget


def _parse_day(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _in_window(now: time, window: str) -> bool:
    try:
        start_raw, end_raw = window.split("-")
        start = time.fromisoformat(start_raw.strip())
        end = time.fromisoformat(end_raw.strip())
    except ValueError:
        return False
    if start <= end:
        return start <= now <= end
    return now >= start or now <= end


def is_blackout(policy: Policy, now: datetime) -> bool:
    if not policy.blackout.enabled:
        return False
    return any(_in_window(now.time(), window) for window in policy.blackout.windows)


def check_campaign_draft(
    policy: Policy, draft: dict, *, active_count: int, now: datetime
) -> list[Violation]:
    violations: list[Violation] = []
    category = draft.get("category")
    max_discount, max_budget = effective_limits(policy, category)

    discount = float(draft.get("discount_pct") or 0)
    if discount > max_discount:
        violations.append(Violation(
            "discount_pct", discount, max_discount,
            f"Indirim %{discount} politika limiti %{max_discount} uzerinde",
        ))

    budget = float(draft.get("budget") or 0)
    if budget > max_budget:
        violations.append(Violation(
            "budget", budget, max_budget,
            f"Butce {budget:,.0f} {policy.currency} limit {max_budget:,.0f} {policy.currency} uzerinde",
        ))

    starts, ends = _parse_day(draft.get("starts_at")), _parse_day(draft.get("ends_at"))
    if starts and ends:
        duration = (ends - starts).days + 1
        if duration > policy.limits.max_campaign_days:
            violations.append(Violation(
                "duration_days", duration, policy.limits.max_campaign_days,
                f"Kampanya suresi {duration} gun, limit {policy.limits.max_campaign_days} gun",
            ))

    if active_count >= policy.limits.max_active_campaigns:
        violations.append(Violation(
            "active_campaigns", active_count + 1, policy.limits.max_active_campaigns,
            f"Aktif kampanya sayisi {active_count + 1} olacak, limit {policy.limits.max_active_campaigns}",
        ))

    if is_blackout(policy, now):
        violations.append(Violation(
            "blackout", now.strftime("%H:%M"), ",".join(policy.blackout.windows),
            "Su an otonom yazma icin blackout penceresi icinde",
        ))

    return violations


def check_budget_change(
    policy: Policy, campaign: dict, new_budget: float, *, daily_delta: float, now: datetime
) -> list[Violation]:
    violations: list[Violation] = []
    _, max_budget = effective_limits(policy, campaign.get("category"))

    if new_budget > max_budget:
        violations.append(Violation(
            "budget", new_budget, max_budget,
            f"Yeni butce {new_budget:,.0f} {policy.currency} limit {max_budget:,.0f} {policy.currency} uzerinde",
        ))

    current = float(campaign.get("budget") or 0)
    delta = abs(new_budget - current)
    if daily_delta + delta > policy.limits.max_daily_budget_delta:
        violations.append(Violation(
            "daily_budget_delta", daily_delta + delta, policy.limits.max_daily_budget_delta,
            f"Gunluk butce degisimi {daily_delta + delta:,.0f} {policy.currency} limiti asiyor",
        ))

    if is_blackout(policy, now):
        violations.append(Violation(
            "blackout", now.strftime("%H:%M"), ",".join(policy.blackout.windows),
            "Su an otonom yazma icin blackout penceresi icinde",
        ))

    return violations


def violations_text(violations: list[Violation]) -> str:
    return " | ".join(violation.message for violation in violations)


def policy_summary(policy: Policy) -> str:
    limits = policy.limits
    lines = [
        f"Para birimi: {policy.currency}",
        f"Otonom limitler: indirim<=%{limits.max_discount_pct:g}, kampanya butcesi<={limits.max_budget_per_campaign:,.0f}, "
        f"gunluk butce degisimi<={limits.max_daily_budget_delta:,.0f}, aktif kampanya<={limits.max_active_campaigns}, "
        f"sure<={limits.max_campaign_days} gun",
        f"Ekonomi hedefleri: min marj %{policy.economics.min_expected_margin_pct:g}, hedef ROAS {policy.economics.target_roi:g}",
    ]
    for category, limit in policy.categories.items():
        parts = []
        if limit.max_discount_pct is not None:
            parts.append(f"indirim<=%{limit.max_discount_pct:g}")
        if limit.max_budget_per_campaign is not None:
            parts.append(f"butce<={limit.max_budget_per_campaign:,.0f}")
        lines.append(f"Kategori '{category}': " + ", ".join(parts))
    if policy.blackout.enabled:
        lines.append(f"Blackout pencereleri: {', '.join(policy.blackout.windows)}")
    return "\n".join(lines)
