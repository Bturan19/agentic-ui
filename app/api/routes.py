from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pydantic_ai.messages import ModelMessagesTypeAdapter

from ..agent.runner import RunInProgressError
from ..context import AppContext
from ..web.transcript import build_transcript

router = APIRouter(prefix="/api")


class MessageIn(BaseModel):
    text: str


class ApprovalIn(BaseModel):
    approved: bool
    note: str | None = None


def _ctx(request: Request) -> AppContext:
    return request.app.state.ctx


@router.get("/state")
async def state(request: Request) -> dict:
    ctx = _ctx(request)
    return {
        "pending_approvals": await ctx.store.pending_approval_count(),
        "conversations": await ctx.store.list_conversations(limit=30),
    }


@router.post("/conversations")
async def create_conversation(request: Request) -> dict:
    ctx = _ctx(request)
    conversation_id = await ctx.store.create_conversation()
    return {"id": conversation_id}


@router.get("/conversations/{conversation_id}/messages")
async def transcript(request: Request, conversation_id: str) -> dict:
    ctx = _ctx(request)
    raw = await ctx.store.get_history(conversation_id)
    if not raw:
        return {"items": []}
    return {"items": build_transcript(ModelMessagesTypeAdapter.validate_json(raw))}


@router.post("/conversations/{conversation_id}/messages", status_code=202)
async def post_message(request: Request, conversation_id: str, body: MessageIn) -> dict:
    ctx = _ctx(request)
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Bos mesaj gonderilemez")
    try:
        run_id = await ctx.runner.start_user_run(conversation_id, text)
    except RunInProgressError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"run_id": run_id}


@router.get("/conversations/{conversation_id}/events")
async def events(request: Request, conversation_id: str) -> StreamingResponse:
    ctx = _ctx(request)
    queue = ctx.bus.subscribe(conversation_id)

    async def generator():
        try:
            yield ": baglandi\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
        finally:
            ctx.bus.unsubscribe(conversation_id, queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.post("/approvals/{approval_id}")
async def resolve_approval(request: Request, approval_id: str, body: ApprovalIn) -> dict:
    ctx = _ctx(request)
    try:
        return await ctx.runner.resolve_approval(approval_id, approved=body.approved, note=body.note)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/approvals")
async def list_approvals(request: Request, status: str | None = "pending") -> list[dict]:
    ctx = _ctx(request)
    return await ctx.store.list_approvals(status=status)


@router.get("/journal")
async def list_journal(request: Request) -> dict:
    ctx = _ctx(request)
    return {"days": ctx.journal.list_days()}


@router.get("/audit")
async def list_audit(request: Request, days: int = 7, kind: str | None = None) -> list[dict]:
    ctx = _ctx(request)
    return await ctx.store.list_audit(days=days, kind=kind)
