from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from fastapi import FastAPI  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from .agent.guards import load_policy  # noqa: E402
from .agent.runner import AgentRunner  # noqa: E402
from .api.integration import router as integration_router  # noqa: E402
from .api.routes import router as api_router  # noqa: E402
from .backend.marketplace import build_marketplace_client  # noqa: E402
from .backend.reporting import build_reporting_client  # noqa: E402
from .context import AppContext  # noqa: E402
from .db import Store  # noqa: E402
from .events import EventBus  # noqa: E402
from .integration.webhooks import WebhookEmitter  # noqa: E402
from .memory.journal import JournalWriter  # noqa: E402
from .memory.learnings import LearningStore  # noqa: E402
from .scheduler.jobs import setup_scheduler  # noqa: E402
from .settings import BASE_DIR, Settings  # noqa: E402
from .settings import settings as default_settings
from .skills.loader import SkillLibrary  # noqa: E402
from .web.views import router as views_router  # noqa: E402
from .web.views import templates  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def create_app(app_settings: Settings | None = None) -> FastAPI:
    settings = app_settings or default_settings

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.ensure_dirs()

        store = Store(settings.db_path)
        await store.connect()
        try:
            bus = EventBus()
            marketplace = build_marketplace_client(settings, store)
            reporting = build_reporting_client(settings, store)
            policy = load_policy(settings.policy_file)
            journal = JournalWriter(settings.journal_path, tz=settings.timezone)
            learnings = LearningStore(settings.memory_path, tz=settings.timezone)
            skills = SkillLibrary(settings.skills_path)
            webhooks = WebhookEmitter(settings.event_webhook_url, settings.integration_token)

            runner = AgentRunner(
                settings=settings,
                store=store,
                bus=bus,
                marketplace=marketplace,
                reporting=reporting,
                policy=policy,
                journal=journal,
                learnings=learnings,
                skills=skills,
                webhooks=webhooks,
            )
            await runner.setup()
        except Exception:
            await store.close()
            raise

        app.state.ctx = AppContext(
            settings=settings,
            store=store,
            bus=bus,
            marketplace=marketplace,
            reporting=reporting,
            policy=policy,
            journal=journal,
            learnings=learnings,
            skills=skills,
            runner=runner,
            webhooks=webhooks,
            templates=templates,
        )

        scheduler = setup_scheduler(runner=runner, store=store, settings=settings)
        logger.info(
            "Hazir | model=%s | marketplace=%s | reporting=%s | webhook=%s | scheduler=%s",
            settings.agent_model,
            type(marketplace).__name__,
            type(reporting).__name__,
            "on" if webhooks.enabled else "off",
            "on" if scheduler else "off",
        )

        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.shutdown(wait=False)
            for client in (marketplace, reporting, webhooks):
                close = getattr(client, "aclose", None)
                if close is not None:
                    await close()
            await store.close()

    app = FastAPI(title="Agentic Kampanya Konsolu", lifespan=lifespan)
    app.include_router(api_router)
    app.include_router(integration_router)
    app.include_router(views_router)
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "web" / "static")), name="static")
    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    run()
