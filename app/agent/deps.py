from __future__ import annotations

from dataclasses import dataclass

from ..backend.marketplace import MarketplaceClient
from ..backend.reporting import ReportingClient
from ..db import Store
from ..memory.journal import JournalWriter
from ..memory.learnings import LearningStore
from ..settings import Settings
from ..skills.loader import SkillLibrary
from .guards import Policy


@dataclass
class AgentDeps:
    store: Store
    marketplace: MarketplaceClient
    reporting: ReportingClient
    policy: Policy
    settings: Settings
    conversation_id: str
    run_id: str
    trigger: str
    journal: JournalWriter
    learnings: LearningStore
    skills: SkillLibrary
