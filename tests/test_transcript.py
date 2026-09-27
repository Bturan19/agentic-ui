from __future__ import annotations

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from app.web.transcript import build_transcript
from app.web.views import _render_markdown


def _messages():
    call = ToolCallPart(
        "propose_campaign",
        {
            "name": "Flaş Ev-Yaşam",
            "budget": 120000,
            "category": "ev-yasam",
            "product_ids": ["P1", "P2"],
            "rationale": "marj ve ROAS verisi",
        },
        tool_call_id="tc_1",
    )
    return [
        ModelRequest(parts=[UserPromptPart("Kampanya aç")]),
        ModelResponse(
            parts=[ThinkingPart("Önce veriyi kontrol edeyim."), TextPart("Öneriyi hazırlıyorum."), call]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="propose_campaign",
                    content={"status": "created", "campaign": {"id": "CMP-1"}},
                    tool_call_id="tc_1",
                )
            ]
        ),
        ModelResponse(parts=[TextPart("Kampanya oluşturuldu.")]),
    ]


def test_transcript_label_brief_ve_durum():
    items = build_transcript(_messages())
    assert items[0] == {"role": "user", "text": "Kampanya aç"}

    assistant = items[1]
    assert assistant["thinking"] == "Önce veriyi kontrol edeyim."
    assert assistant["text"] == "Öneriyi hazırlıyorum."

    call = assistant["tool_calls"][0]
    assert call["label"] == "Kampanya öner"
    assert call["brief"].startswith("name: Flaş Ev-Yaşam")
    assert "product_ids" not in call["brief"]
    assert call["status"] == "ok"
    assert "created" in call["result"]

    assert items[2]["text"] == "Kampanya oluşturuldu."


def test_bilinmeyen_tool_etiketi_ham_isim_kalir():
    items = build_transcript(
        [ModelResponse(parts=[ToolCallPart("custom_tool", {"x": 1}, tool_call_id="tc_9")])]
    )
    assert items[0]["tool_calls"][0]["label"] == "custom_tool"


def test_markdown_xss_temizlenir():
    rendered = _render_markdown("<script>alert(1)</script>\n\n**kalın**")
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "<strong>kalın</strong>" in rendered


def test_markdown_tablo_ve_kod_bloklari():
    rendered = _render_markdown("| a | b |\n|---|---|\n| 1 | 2 |\n\n```sql\nselect 1\n```")
    assert "<table>" in rendered
    assert "select 1" in rendered
