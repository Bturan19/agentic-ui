from __future__ import annotations

import json
from typing import Any

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _normalize(value: Any) -> dict:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {"args": parsed}
        except json.JSONDecodeError:
            return {"raw": value}
    return value if isinstance(value, dict) else {}


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return _json(content)


def _args(value: Any) -> str:
    if isinstance(value, str):
        try:
            return _json(json.loads(value))
        except json.JSONDecodeError:
            return value
    return _json(value or {})


TOOL_LABELS = {
    "list_campaigns": "Kampanyaları listele",
    "get_campaign": "Kampanya detayı",
    "get_campaign_performance": "Kampanya performansı",
    "portfolio_summary": "Portföy özeti",
    "category_product_performance": "Kategori ürün performansı",
    "propose_campaign": "Kampanya öner",
    "update_campaign_budget": "Bütçe güncelle",
    "pause_campaign": "Kampanyayı durdur",
    "resume_campaign": "Kampanyayı başlat",
    "save_learning": "Öğrenim kaydet",
    "write_journal": "Journal yaz",
    "read_journal": "Journal oku",
    "get_recent_audit": "Audit oku",
    "list_skills": "Skill listesi",
    "read_skill": "Skill oku",
    "get_policy": "Politika oku",
    "bigquery_query": "BigQuery sorgusu",
}


def tool_label(name: str) -> str:
    return TOOL_LABELS.get(name, name)


def _brief(args: dict, max_items: int = 2, max_length: int = 42) -> str:
    if not isinstance(args, dict):
        return ""
    parts: list[str] = []
    for key, value in args.items():
        if isinstance(value, list):
            text = f"{len(value)} kayıt"
        elif isinstance(value, dict):
            text = "…"
        else:
            text = str(value)
        if len(text) > max_length:
            text = text[:max_length] + "…"
        parts.append(f"{key}: {text}")
        if len(parts) == max_items:
            break
    return " · ".join(parts)


def build_transcript(messages: list) -> list[dict]:
    items: list[dict] = []
    calls: dict[str, dict] = {}

    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    items.append({"role": "user", "text": _text(part.content)})
                elif isinstance(part, ToolReturnPart):
                    call = calls.get(part.tool_call_id)
                    if call is not None:
                        call["result"] = _text(part.content)
                        call["status"] = "ok"
                elif isinstance(part, RetryPromptPart):
                    call = calls.get(getattr(part, "tool_call_id", "") or "")
                    if call is not None:
                        call["status"] = "retry"
                        call["result"] = _text(part.content)
        elif isinstance(message, ModelResponse):
            item = {"role": "assistant", "text": "", "thinking": "", "tool_calls": []}
            for part in message.parts:
                if isinstance(part, TextPart):
                    item["text"] += part.content
                elif isinstance(part, ThinkingPart):
                    item["thinking"] += part.content
                elif isinstance(part, ToolCallPart):
                    call = {
                        "id": part.tool_call_id,
                        "name": part.tool_name,
                        "label": tool_label(part.tool_name),
                        "brief": _brief(_normalize(part.args)),
                        "args": _args(part.args),
                        "result": None,
                        "status": "pending",
                    }
                    item["tool_calls"].append(call)
                    calls[part.tool_call_id] = call
            if item["text"] or item["thinking"] or item["tool_calls"]:
                items.append(item)

    return items
