from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from app.agent.guards import Policy
from app.agent.runner import AgentRunner
from app.backend.marketplace import MockMarketplaceClient
from app.backend.reporting import MockReportingClient
from app.db import Store
from app.events import EventBus
from app.integration.webhooks import WebhookEmitter
from app.memory.journal import JournalWriter
from app.memory.learnings import LearningStore
from app.settings import BASE_DIR, Settings
from app.skills.loader import SkillLibrary


def _has_tool_return(messages) -> bool:
    return any(
        isinstance(part, ToolReturnPart) for message in messages for part in getattr(message, "parts", [])
    )


def _make_handler(call: ToolCallPart):
    def handler(messages, info):
        if _has_tool_return(messages):
            return ModelResponse(parts=[TextPart("Aksiyon tamamlandi.")])
        return ModelResponse(parts=[call])

    async def stream_handler(messages, info):
        if _has_tool_return(messages):
            yield "Aksiyon tamamlandi."
        else:
            yield {
                0: DeltaToolCall(
                    name=call.tool_name,
                    json_args=json.dumps(call.args),
                    tool_call_id="test_call_1",
                )
            }

    return handler, stream_handler


def _function_model(call: ToolCallPart) -> FunctionModel:
    handler, stream_handler = _make_handler(call)
    return FunctionModel(handler, stream_function=stream_handler)


def _tool_call(discount_pct: float = 35, budget: float = 120000) -> ToolCallPart:
    return ToolCallPart(
        "propose_campaign",
        {
            "name": "Flas Ev-Yasam Indirimi",
            "category": "ev-yasam",
            "discount_pct": discount_pct,
            "budget": budget,
            "starts_at": "2026-09-28",
            "ends_at": "2026-09-30",
            "product_ids": ["P1", "P2"],
            "rationale": "Son 30 gunun marj ve donusum verisine gore",
        },
    )


def _webhook_emitter(captured: list[dict]) -> WebhookEmitter:
    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content.decode()))
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return WebhookEmitter("http://hooks.local/events", token="tok", client=client)


@asynccontextmanager
async def _runner(tmp_path: Path, call: ToolCallPart, webhooks: WebhookEmitter | None = None):
    store = Store(tmp_path / "test.db")
    await store.connect()
    try:
        settings = Settings(
            database_path=str(tmp_path / "test.db"),
            journal_dir=str(tmp_path / "journal"),
            memory_dir=str(tmp_path / "memory"),
        )
        runner = AgentRunner(
            settings=settings,
            store=store,
            bus=EventBus(),
            marketplace=MockMarketplaceClient(store),
            reporting=MockReportingClient(store),
            policy=Policy(),
            journal=JournalWriter(tmp_path / "journal"),
            learnings=LearningStore(tmp_path / "memory"),
            skills=SkillLibrary(BASE_DIR / "skills"),
            webhooks=webhooks,
            model_override=_function_model(call),
        )
        await runner.setup()
        yield runner, store
    finally:
        await store.close()


async def test_limit_ustu_aksiyon_onaya_duser_ve_onayla_uygulanir(tmp_path):
    async with _runner(tmp_path, _tool_call(discount_pct=35, budget=120000)) as (runner, store):
        conversation_id = await store.create_conversation()
        run_id = await runner.start_user_run(conversation_id, "Ev-yasamda flas indirim yap")
        await runner.wait_idle(conversation_id)

        run = await store.get_run(run_id)
        assert run["status"] == "waiting_approval"

        pending = await store.list_approvals(status="pending", conversation_id=conversation_id)
        assert len(pending) == 1
        approval = pending[0]
        assert approval["tool_name"] == "propose_campaign"
        assert approval["reason"] and "Indirim" in approval["reason"]
        assert await store.list_campaigns() == []

        result = await runner.resolve_approval(approval["id"], approved=True, note="test onayi")
        assert result == {"resolved": True, "resumed": True, "status": "approved"}
        await runner.wait_idle(conversation_id)

        assert (await store.get_run(run_id))["status"] == "done"
        campaigns = await store.list_campaigns()
        assert len(campaigns) == 1
        assert campaigns[0]["discount_pct"] == 35


async def test_limit_ustu_aksiyon_reddedilirse_uygulanmaz(tmp_path):
    async with _runner(tmp_path, _tool_call(discount_pct=35, budget=120000)) as (runner, store):
        conversation_id = await store.create_conversation()
        run_id = await runner.start_user_run(conversation_id, "Kampanya yap")
        await runner.wait_idle(conversation_id)

        approval = (await store.list_approvals(status="pending", conversation_id=conversation_id))[0]
        await runner.resolve_approval(approval["id"], approved=False, note="butce uygun degil")
        await runner.wait_idle(conversation_id)

        assert (await store.get_run(run_id))["status"] == "done"
        assert await store.list_campaigns() == []


async def test_limit_ici_aksiyon_otonom_uygulanir(tmp_path):
    async with _runner(tmp_path, _tool_call(discount_pct=10, budget=10000)) as (runner, store):
        conversation_id = await store.create_conversation()
        run_id = await runner.start_user_run(conversation_id, "Ev-yasam kampanyasi")
        await runner.wait_idle(conversation_id)

        assert (await store.get_run(run_id))["status"] == "done"
        assert len(await store.list_campaigns()) == 1
        assert await store.list_approvals(status="pending") == []


async def test_audit_kayitlari_olusur(tmp_path):
    async with _runner(tmp_path, _tool_call(discount_pct=10, budget=10000)) as (runner, store):
        conversation_id = await store.create_conversation()
        await runner.start_user_run(conversation_id, "Ev-yasam kampanyasi")
        await runner.wait_idle(conversation_id)

        kinds = {entry["kind"] for entry in await store.list_audit(days=1)}
        assert {"user_message", "tool_call", "tool_result"} <= kinds


async def test_webhook_olaylari_yayinlanir(tmp_path):
    captured: list[dict] = []
    async with _runner(
        tmp_path, _tool_call(discount_pct=35, budget=120000), webhooks=_webhook_emitter(captured)
    ) as (runner, store):
        conversation_id = await store.create_conversation()
        await runner.start_user_run(conversation_id, "Kampanya ac")
        await runner.wait_idle(conversation_id)

        approval = (await store.list_approvals(status="pending", conversation_id=conversation_id))[0]
        await runner.resolve_approval(approval["id"], approved=True, note="ok")
        await runner.wait_idle(conversation_id)

    events = [item["event"] for item in captured]
    assert "approval_required" in events
    assert "approval_resolved" in events
    assert "campaign_action" in events
    assert events.count("run_finished") >= 2

    action = next(item for item in captured if item["event"] == "campaign_action")
    assert action["action"] == "propose_campaign"
    assert action["result"]["status"] == "created"

    finished = [item for item in captured if item["event"] == "run_finished"]
    assert any(item["status"] == "waiting_approval" for item in finished)
    assert any(item["status"] == "done" for item in finished)
