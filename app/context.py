from __future__ import annotations

from dataclasses import dataclass

from fastapi.templating import Jinja2Templates

from .agent.guards import Policy
from .agent.runner import AgentRunner
from .backend.marketplace import MarketplaceClient
from .backend.reporting import ReportingClient
from .db import Store
from .events import EventBus
from .integration.webhooks import WebhookEmitter
from .memory.journal import JournalWriter
from .memory.learnings import LearningStore
from .settings import Settings
from .skills.loader import SkillLibrary


@dataclass
class AppContext:
    settings: Settings
    store: Store
    bus: EventBus
    marketplace: MarketplaceClient
    reporting: ReportingClient
    policy: Policy
    journal: JournalWriter
    learnings: LearningStore
    skills: SkillLibrary
    runner: AgentRunner
    webhooks: WebhookEmitter
    templates: Jinja2Templates
