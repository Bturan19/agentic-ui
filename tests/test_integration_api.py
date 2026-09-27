from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from app.main import create_app
from app.settings import Settings


def _text_handler(messages, info):
    return ModelResponse(parts=[TextPart("Sinyal degerlendirildi.")])


async def _text_stream(messages, info):
    yield "Sinyal degerlendirildi."


def _settings(tmp_path: Path, token: str = "test-token") -> Settings:
    return Settings(
        database_path=str(tmp_path / "app.db"),
        journal_dir=str(tmp_path / "journal"),
        memory_dir=str(tmp_path / "memory"),
        scheduler_enabled=False,
        integration_token=token,
    )


@pytest.fixture
async def api(tmp_path: Path):
    app = create_app(_settings(tmp_path))
    async with app.router.lifespan_context(app):
        app.state.ctx.runner._model_override = FunctionModel(
            function=_text_handler, stream_function=_text_stream
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", follow_redirects=True
        ) as client:
            yield app, client


SIGNAL = {
    "case": "flash-indirim-firsati",
    "signal_id": "SIG-1001",
    "title": "Ev-Yaşam flaş fırsatı",
    "payload": {"category": "ev-yasam", "roas": 2.4, "stok_gunu": 9},
    "proposed_action": {"type": "campaign", "discount_pct": 20},
    "rationale": "Kategori ROAS ortalamanin uzerinde",
    "source": "bq-scheduler",
}


async def test_token_ayarlanmamissa_503(tmp_path):
    app = create_app(_settings(tmp_path, token=""))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/signals", json=SIGNAL)
            assert response.status_code == 503


async def test_yanlis_token_401(api):
    _, client = api
    response = await client.post(
        "/api/signals", json=SIGNAL, headers={"Authorization": "Bearer yanlis-token"}
    )
    assert response.status_code == 401


async def test_sinyal_konusma_ve_run_tetikler(api):
    app, client = api
    response = await client.post(
        "/api/signals", json=SIGNAL, headers={"Authorization": "Bearer test-token"}
    )
    assert response.status_code == 202
    body = response.json()
    assert body["conversation_id"].startswith("conv_")
    assert body["run_id"]

    await app.state.ctx.runner.wait_idle(body["conversation_id"])

    conversation = await app.state.ctx.store.get_conversation(body["conversation_id"])
    assert conversation["kind"] == "signal"
    assert conversation["title"].startswith("Ev-Yaşam")

    signal_audit = await app.state.ctx.store.list_audit(days=1, kind="signal_received")
    assert len(signal_audit) == 1
    assert signal_audit[0]["name"] == "flash-indirim-firsati"

    run = await app.state.ctx.store.get_run(body["run_id"])
    assert run["trigger"] == "signal"
    assert run["status"] == "done"


async def test_batch_sinyal(api):
    app, client = api
    response = await client.post(
        "/api/signals/batch",
        json=[SIGNAL, {**SIGNAL, "signal_id": "SIG-1002", "case": "stok-eritme"}],
        headers={"X-Integration-Token": "test-token"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["accepted"] == 2
    for item in body["signals"]:
        await app.state.ctx.runner.wait_idle(item["conversation_id"])
    conversations = await app.state.ctx.store.list_conversations()
    assert len(conversations) == 2


async def test_auto_run_kapali(api):
    app, client = api
    response = await client.post(
        "/api/signals",
        json={**SIGNAL, "auto_run": False},
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["run_id"] is None
    assert await app.state.ctx.store.active_run(body["conversation_id"]) is None
