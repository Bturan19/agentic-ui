from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..agent.runner import AgentRunner
from ..db import Store
from ..settings import Settings

logger = logging.getLogger(__name__)

DAILY_REVIEW_PROMPT = """\
Otonom gunluk portfoy taramasi calistiriyorsun (kullanici tetiklemedi).
Su adimlari izle:
1. portfolio_summary(days=7) ile portfoyun tamamina bak.
2. Anormallikleri, firsatlari ve riskleri belirle.
3. Gerekli aksiyonlar icin tool'lari cagir (limit ustu aksiyonlar onay kuyruguna dusecek; bu beklenen davranistir).
4. Kisa ve operasyonel bir yonetici ozeti yaz (dikkat gerektiren 3 madde + onerilen aksiyonlar).
5. write_journal ile ozet, aksiyonlar ve ogrenimleri kaydet.
Tum yanitlar Turkce olmali."""

WEEKLY_LEARNING_PROMPT = """\
Haftalik ogrenim derleme calismasi calistiriyorsun (kullanici tetiklemedi).
Su adimlari izle:
1. get_recent_audit(days=7) ve read_journal(days=7) ile son haftayi incele.
2. Tekrar eden kaliplari, iyi/kotu performans nedenlerini ve ise yarayan yaklasimlari cikar.
3. Kalici degeri olan ogrenimleri save_learning ile kaydet.
4. write_journal ile haftalik ozet yaz.
Tum yanitlar Turkce olmali."""


def _cron_trigger(expression: str, timezone: str) -> CronTrigger:
    minute, hour, day, month, day_of_week = expression.split()
    return CronTrigger(
        minute=minute, hour=hour, day=day, month=month, day_of_week=day_of_week, timezone=timezone
    )


async def _start_job(runner: AgentRunner, store: Store, *, title: str, prompt: str) -> None:
    conversation_id = await store.create_conversation(kind="scheduled", title=title)
    await runner.start_scheduled_run(conversation_id, prompt)
    logger.info("Otonom calisma baslatildi: %s (%s)", title, conversation_id)


def setup_scheduler(*, runner: AgentRunner, store: Store, settings: Settings) -> AsyncIOScheduler | None:
    if not settings.scheduler_enabled:
        return None

    scheduler = AsyncIOScheduler(timezone=settings.timezone)

    async def daily_review() -> None:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        today = datetime.now(ZoneInfo(settings.timezone)).date().isoformat()
        await _start_job(runner, store, title=f"Günlük portföy taraması — {today}", prompt=DAILY_REVIEW_PROMPT)

    async def weekly_learning() -> None:
        await _start_job(runner, store, title="Haftalık öğrenim derlemesi", prompt=WEEKLY_LEARNING_PROMPT)

    scheduler.add_job(
        daily_review,
        _cron_trigger(settings.daily_review_cron, settings.timezone),
        id="daily_review",
        replace_existing=True,
    )
    scheduler.add_job(
        weekly_learning,
        _cron_trigger(settings.weekly_learning_cron, settings.timezone),
        id="weekly_learning",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler aktif: %s | %s", settings.daily_review_cron, settings.weekly_learning_cron)
    return scheduler
