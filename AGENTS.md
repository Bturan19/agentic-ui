# AGENTS.md — Geliştirici notları

Bu repo, pazaryeri kampanya portföyü için tarayıcı tabanlı agentic konsoldur.
Aşağısı bu depoda çalışacak kod ajanları içindir.

## Komutlar

```bash
uv sync                          # bagimliliklar
uv run uvicorn app.main:app --reload   # gelistirme sunucusu (http://127.0.0.1:8000)
uv run pytest                    # testler (mock model; gercek LLM cagrisi yok)
uv run ruff check .              # lint
uv run ruff check . --fix        # otomatik duzeltme
```

## Mimari haritası

- `app/main.py` — `create_app()` factory + lifespan (Store, EventBus, marketplace, runner, scheduler).
- `app/agent/runner.py` — run yaşam döngüsü: `run_stream_events()` → SSE olayları, DeferredTool bahisleri,
  onay çözümü ve resume, audit yazımı, history kalıcılığı.
- `app/agent/core.py` — `build_agent()`: model, output_type `[str, DeferredToolRequests]`, toolset'ler.
- `app/agent/tools.py` — tool kayıtları. Bağlamlı tool'lar `@agent.tool`, bağlamsız olanlar `@agent.tool_plain`.
- `app/agent/guards.py` — `config/policy.yaml` modeli ve kontroller. Limit ihlali → `ApprovalRequired`.
- `app/agent/toolsets.py` — MCP Toolbox bağlantısı (initialize doğrulamalı) ve stub fallback.
- `app/backend/marketplace.py` — kampanya okuma/yazma adaptörleri (mock | HTTP back office | Postgres).
- `app/backend/reporting.py` — analitik adaptörleri (mock | HTTP | Postgres); performans/portföy/
  kategori tool'ları bu arayüzü kullanır.
- `app/backend/metrics.py` — mock metrik üreticileri ve `aggregate` (mock + test ortak).
- `app/integration/webhooks.py` — dış webhook yayıncısı ve tool payload parser.
- `app/api/integration.py` — `POST /api/signals` ve `/api/signals/batch` (Bearer `INTEGRATION_TOKEN`).
- `docs/integration.md` — dış sistem sözleşmeleri ve fork-and-adapt kontrol listesi.
- `app/db.py` — SQLite (aiosqlite) şema ve erişim. `run_state.history_json` Pydantic AI
  `ModelMessagesTypeAdapter` formatında saklanır.
- `app/web/transcript.py` — `ModelMessages` → UI item'ları dönüşümü.
- `app/web/views.py` + `templates/` — sayfalar; `partials/` HTMX parçaları.
- `app/web/static/app.js` — SSE tüketicisi, canlı stream render'ı, onay butonları.
- `skills/<ad>/SKILL.md` — ajana `read_skill` ile verilen prosedürler.
- `config/agent.md` — ajanın system prompt'u (çalışma zamanında okunur; restart gerekmez).

## Kritik kurallar

1. **Guardrail server-side'dır.** Limit kontrolleri tool fonksiyonlarının içinde yapılır;
   prompt metnine güvenilmez. Yeni yazma tool'u eklerken `guards` kontrolünü ekleyin.
2. **Pydantic AI v2 API'si:** `AgentRunResultEvent` üst düzey `pydantic_ai` paketinden gelir.
   `FunctionModel` ile stream test ederken `stream_function` zorunludur.
3. **Resume kuralı:** onay sonrası devam koşusunda `run_id` **geçirilmez** (message_history
   ile çakışır); `conversation_id` korelasyon için yeterlidir.
4. **Store kapatma:** aiosqlite thread'i non-daemon'dur; test/script'lerde Store mutlaka
   `finally` içinde `close()` edilmelidir, aksi halde süreç asılı kalır.
5. **Olay tipleri** (`app/agent/runner.py` ↔ `app.js`): `user_message`, `run_started`,
   `text_delta`, `thinking_delta`, `tool_call`, `tool_result`, `approval_required`,
   `approval_resolved`, `run_finished`, `error`. İki tarafı birlikte güncelleyin.
6. **Türkçe içerik:** system prompt, tool docstring'leri, UI metinleri ve journal Türkçe'dir.
   Kod yorumları minimumda tutulur.
7. **Testler mock model kullanır** (`FunctionModel`); gerçek Vertex/Gemini çağrısı yapılmaz,
   API anahtarı gerekmez.
8. **Domain ayrımı:** performans/raporlama verisi `ReportingClient`'tan, kampanya yazma
   `MarketplaceClient`'tan gelir. Yeni analitik tool'ları reporting'e, yazma tool'ları
   marketplace'e eklenir; ajan tool'ları bu iki arayüzü karıştırmaz.
9. **Dış sözleşmeler kırılmaz:** `/api/signals` gövdesi, webhook event tipleri ve SSE olay
   tipleri `docs/integration.md`'de tanımlıdır; değişiklikte dokümanı ve testleri birlikte
   güncelleyin.
