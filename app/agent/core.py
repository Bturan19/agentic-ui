from __future__ import annotations

from pydantic_ai import Agent, DeferredToolRequests

from ..settings import Settings
from .deps import AgentDeps
from .prompts import build_instructions
from .tools import register_campaign_tools, register_memory_tools
from .toolsets import build_mcp_toolsets, register_bigquery_stub


async def build_agent(settings: Settings) -> Agent[AgentDeps]:
    toolsets = await build_mcp_toolsets(settings)

    agent: Agent[AgentDeps] = Agent(
        settings.agent_model,
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
        name="campaign-agent",
        instructions=build_instructions,
        toolsets=toolsets,
        defer_model_check=True,
    )

    register_campaign_tools(agent)
    register_memory_tools(agent)
    if not toolsets:
        register_bigquery_stub(agent)

    return agent
