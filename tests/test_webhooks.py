from __future__ import annotations

import json

import httpx

from app.integration.webhooks import WebhookEmitter, parse_tool_payload


async def test_emitter_olay_gonderir():
    captured: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            {"body": json.loads(request.content.decode()), "auth": request.headers.get("authorization")}
        )
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    emitter = WebhookEmitter("http://hooks.local/events", token="tok", client=client)
    await emitter.emit("approval_required", {"approval_id": "a1", "tool_name": "propose_campaign"})
    await emitter.aclose()

    assert len(captured) == 1
    body = captured[0]["body"]
    assert body["event"] == "approval_required"
    assert body["approval_id"] == "a1"
    assert body["ts"]
    assert captured[0]["auth"] == "Bearer tok"


async def test_emitter_kapaliysa_gondermez():
    captured: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append({"body": json.loads(request.content.decode())})
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    emitter = WebhookEmitter("", client=client)
    await emitter.emit("run_finished", {"run_id": "r1"})
    await emitter.aclose()
    assert captured == []


async def test_emitter_hata_run_bozmaz():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    emitter = WebhookEmitter("http://hooks.local/events", client=client)
    await emitter.emit("run_finished", {"run_id": "r1"})
    await emitter.aclose()


def test_parse_tool_payload():
    assert parse_tool_payload({"status": "created"}) == {"status": "created"}
    assert parse_tool_payload('{"status": "created", "campaign": {"id": "C1"}}') == {
        "status": "created",
        "campaign": {"id": "C1"},
    }
    assert parse_tool_payload("duz metin") == {"text": "duz metin"}
