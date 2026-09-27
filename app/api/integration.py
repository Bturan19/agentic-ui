"""Sirket ici sistemlerden sinyal alma (ingestion) endpoint'leri."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..agent.runner import RunInProgressError
from ..context import AppContext
from ..integration.prompts import signal_prompt

router = APIRouter(prefix="/api", tags=["integration"])


class SignalIn(BaseModel):
    case: str
    signal_id: str | None = None
    title: str | None = None
    payload: dict = Field(default_factory=dict)
    proposed_action: dict | None = None
    rationale: str | None = None
    source: str | None = None
    auto_run: bool = True


def _ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def _require_token(request: Request) -> None:
    token = _ctx(request).settings.integration_token
    if not token:
        raise HTTPException(
            status_code=503,
            detail="Entegrasyon kapali: .env icinde INTEGRATION_TOKEN ayarlayin",
        )
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        provided = header[7:].strip()
    else:
        provided = request.headers.get("x-integration-token", "")
    if not provided or not secrets.compare_digest(provided, token):
        raise HTTPException(status_code=401, detail="Gecersiz entegrasyon token'i")


async def _ingest(request: Request, signal: SignalIn) -> dict:
    ctx = _ctx(request)
    title = (signal.title or f"Sinyal: {signal.case}")[:120]
    conversation_id = await ctx.store.create_conversation(kind="signal", title=title)
    await ctx.store.audit(
        "signal_received",
        conversation_id=conversation_id,
        name=signal.case,
        payload=signal.model_dump(),
    )
    run_id = None
    if signal.auto_run:
        try:
            run_id = await ctx.runner.start_signal_run(
                conversation_id, signal_prompt(signal.model_dump())
            )
        except RunInProgressError:
            run_id = None
    return {"conversation_id": conversation_id, "run_id": run_id, "url": f"/c/{conversation_id}"}


@router.post("/signals", status_code=202)
async def receive_signal(request: Request, body: SignalIn) -> dict:
    """Tek sinyal alir; yeni bir konusma acip ajan run'ini tetikler."""
    _require_token(request)
    return await _ingest(request, body)


@router.post("/signals/batch", status_code=202)
async def receive_signals(request: Request, body: list[SignalIn]) -> dict:
    """Toplu sinyal alir (scheduler ciktilari icin)."""
    _require_token(request)
    results = [await _ingest(request, signal) for signal in body]
    return {"accepted": len(results), "signals": results}
