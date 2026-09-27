from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from app.main import create_app
from app.settings import Settings


def _text_handler(messages, info):
    return ModelResponse(parts=[TextPart("Merhaba! Portfoy hazir.")])


async def _text_stream(messages, info):
    yield "Merhaba! Portfoy hazir."


@pytest.fixture
async def app_and_client(tmp_path: Path):
    app_settings = Settings(
        database_path=str(tmp_path / "app.db"),
        journal_dir=str(tmp_path / "journal"),
        memory_dir=str(tmp_path / "memory"),
        scheduler_enabled=False,
    )
    app = create_app(app_settings)
    async with app.router.lifespan_context(app):
        app.state.ctx.runner._model_override = FunctionModel(
            function=_text_handler, stream_function=_text_stream
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", follow_redirects=True
        ) as client:
            yield app, client


async def test_sayfalar_acilis(app_and_client):
    _, client = app_and_client
    for path in ["/", "/journal", "/audit"]:
        response = await client.get(path)
        assert response.status_code == 200, path


async def test_mesaj_gonder_ve_transcript_oku(app_and_client):
    app, client = app_and_client

    created = await client.post("/api/conversations")
    conversation_id = created.json()["id"]

    response = await client.post(
        f"/api/conversations/{conversation_id}/messages", json={"text": "Portfoyu ozetle"}
    )
    assert response.status_code == 202

    await app.state.ctx.runner.wait_idle(conversation_id)

    transcript = await client.get(f"/api/conversations/{conversation_id}/messages")
    items = transcript.json()["items"]
    assert items[0] == {"role": "user", "text": "Portfoyu ozetle"}
    assert any(item.get("text") == "Merhaba! Portfoy hazir." for item in items)

    chat_page = await client.get(f"/c/{conversation_id}")
    assert "Portfoyu ozetle" in chat_page.text


async def test_bos_mesaj_reddedilir(app_and_client):
    _, client = app_and_client
    created = await client.post("/api/conversations")
    conversation_id = created.json()["id"]
    response = await client.post(f"/api/conversations/{conversation_id}/messages", json={"text": "   "})
    assert response.status_code == 400


async def test_olmayan_konusma_404(app_and_client):
    _, client = app_and_client
    response = await client.get("/c/conv_yok")
    assert response.status_code == 404
