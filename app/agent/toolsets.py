from __future__ import annotations

import logging

import httpx
from pydantic_ai import Agent

from ..settings import Settings
from .deps import AgentDeps

logger = logging.getLogger(__name__)

STUB_ROWS = [
    {"campaign_id": "CMP-DEMO01", "date": "2026-09-26", "revenue": 184500, "margin": 22100, "orders": 412},
    {"campaign_id": "CMP-DEMO02", "date": "2026-09-26", "revenue": 96750, "margin": 5400, "orders": 233},
]


async def toolbox_available(url: str, timeout: float = 2.5) -> bool:
    """MCP Toolbox'un gercekten ayakta oldugunu JSON-RPC initialize ile dogrular.

    Basit bir GET kontrolu yeterli degildir: portu dinleyen baska bir servis
    (orn. macOS AirPlay :5000) yanlis pozitif uretebilir.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "agentic-ui", "version": "0.1.0"},
        },
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                url, json=payload, headers={"Accept": "application/json, text/event-stream"}
            )
    except Exception:
        return False
    if response.status_code >= 500:
        return False
    content_type = response.headers.get("content-type", "")
    if "json" not in content_type and "event-stream" not in content_type:
        return False
    return '"jsonrpc"' in response.text


async def build_mcp_toolsets(settings: Settings) -> list:
    """MCP Toolbox ayakta ise BigQuery toolset'ini dondurur, degilse bos liste."""
    if not settings.toolbox_mcp_url:
        return []
    if not await toolbox_available(settings.toolbox_mcp_url):
        logger.warning("MCP Toolbox erisilemedi (%s); bigquery_query stub modunda", settings.toolbox_mcp_url)
        return []
    from pydantic_ai.mcp import MCPToolset

    return [MCPToolset(settings.toolbox_mcp_url, id="toolbox")]


def register_bigquery_stub(agent: Agent[AgentDeps]) -> None:
    @agent.tool_plain
    async def bigquery_query(sql: str, max_rows: int = 50) -> dict:
        """(STUB) BigQuery sorgusu calistirir. MCP Toolbox bagli degilken ornek veri doner."""
        return {
            "status": "stub",
            "note": "MCP Toolbox baglantisi yok; ornek veri donuluyor. Gercek BigQuery erisimi icin TOOLBOX_MCP_URL ayarlayin.",
            "sql": sql,
            "max_rows": max_rows,
            "rows": STUB_ROWS,
        }
