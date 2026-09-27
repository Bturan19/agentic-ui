from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic_ai import RunContext

from . import guards
from .deps import AgentDeps


def build_instructions(ctx: RunContext[AgentDeps]) -> str:
    deps = ctx.deps
    now = datetime.now(ZoneInfo(deps.settings.timezone))

    sections: list[str] = []
    agent_md = deps.settings.agent_md_file
    if agent_md.exists():
        sections.append(agent_md.read_text(encoding="utf-8").strip())

    sections.append("## Politika Ozeti\n" + guards.policy_summary(deps.policy))
    sections.append("## Kullanilabilir Skill'ler\n" + deps.skills.index_markdown())
    sections.append("## Kalici Ogrenimler\n" + deps.learnings.read())
    sections.append(
        "## Baglam\n"
        f"Simdi: {now:%Y-%m-%d %H:%M} ({deps.settings.timezone})\n"
        f"Tetikleyici: {deps.trigger}\n"
        f"Konusma: {deps.conversation_id}"
    )
    return "\n\n".join(section for section in sections if section.strip())
