from __future__ import annotations

import html
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import markdown as md
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic_ai.messages import ModelMessagesTypeAdapter

from ..context import AppContext
from ..settings import BASE_DIR, settings
from .transcript import build_transcript

router = APIRouter()

templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "web" / "templates"))


def _localtime(value: str) -> str:
    try:
        return datetime.fromisoformat(value).astimezone(ZoneInfo(settings.timezone)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return value or ""


def _render_markdown(text: str) -> str:
    """XSS-guvenli markdown: once HTML escape, sonra markdown (LLM/BQ verisi ham HTML icerebilir)."""
    escaped = html.escape(text or "")
    return md.markdown(escaped, extensions=["extra", "sane_lists", "nl2br"])


templates.env.filters["localtime"] = _localtime
templates.env.filters["markdown"] = _render_markdown


def _ctx(request: Request) -> AppContext:
    return request.app.state.ctx


async def _transcript_items(ctx: AppContext, conversation_id: str) -> list[dict]:
    raw = await ctx.store.get_history(conversation_id)
    if not raw:
        return []
    return build_transcript(ModelMessagesTypeAdapter.validate_json(raw))


async def _pending_approvals(ctx: AppContext, conversation_id: str) -> list[dict]:
    rows = await ctx.store.list_approvals(status="pending", conversation_id=conversation_id)
    shaped = []
    for row in rows:
        try:
            args = json.loads(row["args_json"] or "{}")
        except json.JSONDecodeError:
            args = {"raw": row["args_json"]}
        shaped.append(
            {
                "id": row["id"],
                "tool_name": row["tool_name"],
                "reason": row["reason"],
                "created_at": row["created_at"],
                "args_pretty": json.dumps(args, ensure_ascii=False, indent=2),
            }
        )
    return shaped


async def _feed_context(ctx: AppContext, conversation_id: str) -> dict:
    return {
        "items": await _transcript_items(ctx, conversation_id),
        "pending_approvals": await _pending_approvals(ctx, conversation_id),
    }


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> RedirectResponse:
    ctx = _ctx(request)
    conversations = await ctx.store.list_conversations(limit=1)
    if conversations:
        return RedirectResponse(f"/c/{conversations[0]['id']}")
    conversation_id = await ctx.store.create_conversation()
    return RedirectResponse(f"/c/{conversation_id}")


@router.get("/c/{conversation_id}", response_class=HTMLResponse)
async def chat(request: Request, conversation_id: str) -> HTMLResponse:
    ctx = _ctx(request)
    conversation = await ctx.store.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Konusma bulunamadi")
    active_run = await ctx.store.active_run(conversation_id)
    active_status = active_run["status"] if active_run else "done"
    conversations = await ctx.store.list_conversations()
    context = {
        "conversation": conversation,
        "conversations": conversations,
        "active_status": active_status,
        "active_status_label": {
            "running": "Çalışıyor…",
            "waiting_approval": "Onay bekliyor",
            "done": "Hazır",
            "error": "Hata",
        }.get(active_status, active_status),
    }
    context.update(await _feed_context(ctx, conversation_id))
    return templates.TemplateResponse(request, "chat.html", context)


@router.get("/partials/transcript/{conversation_id}", response_class=HTMLResponse)
async def transcript_partial(request: Request, conversation_id: str) -> HTMLResponse:
    ctx = _ctx(request)
    return templates.TemplateResponse(request, "partials/_feed.html", await _feed_context(ctx, conversation_id))


@router.get("/partials/badge", response_class=HTMLResponse)
async def badge(request: Request) -> HTMLResponse:
    ctx = _ctx(request)
    count = await ctx.store.pending_approval_count()
    return templates.TemplateResponse(request, "partials/_badge.html", {"pending_count": count})


@router.get("/journal", response_class=HTMLResponse)
async def journal(request: Request, day: str | None = None) -> HTMLResponse:
    ctx = _ctx(request)
    days = ctx.journal.list_days()
    selected = day if day in days else (days[0] if days else None)
    content_md = ctx.journal.read_day(selected) if selected else ""
    content_html = md.markdown(content_md, extensions=["extra", "nl2br", "sane_lists"]) if content_md else ""
    return templates.TemplateResponse(
        request,
        "journal.html",
        {"days": days, "selected": selected, "content_html": content_html},
    )


@router.get("/audit", response_class=HTMLResponse)
async def audit(request: Request, days: int = 7, kind: str | None = None) -> HTMLResponse:
    ctx = _ctx(request)
    entries = await _audit_entries(ctx, days=days, kind=kind)
    return templates.TemplateResponse(
        request, "audit.html", {"days": days, "kind": kind, "entries": entries}
    )


async def _audit_entries(ctx: AppContext, *, days: int, kind: str | None) -> list[dict]:
    rows = await ctx.store.list_audit(days=days, kind=kind or None, limit=400)
    entries = []
    for row in rows:
        payload = row.get("payload_json") or ""
        if len(payload) > 400:
            payload = payload[:400] + "…"
        entries.append(
            {
                "ts": row["ts"],
                "kind": row["kind"],
                "name": row["name"] or "",
                "payload": payload,
                "conversation_id": row["conversation_id"] or "",
            }
        )
    return entries


@router.get("/partials/audit-table", response_class=HTMLResponse)
async def audit_table(request: Request, days: int = 7, kind: str | None = None) -> HTMLResponse:
    ctx = _ctx(request)
    entries = await _audit_entries(ctx, days=days, kind=kind)
    return templates.TemplateResponse(request, "partials/_audit_table.html", {"entries": entries})
