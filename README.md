# Agentic Kampanya Konsolu

**Agentic console for e-commerce marketplace campaign portfolios.** Chat with an AI agent that
queries BigQuery (via MCP Toolbox), opens/pauses campaigns through your back-office API, enforces
budget guardrails server-side, and pauses for human approval above limits. Ships with markdown
skills, persistent learnings, journal, audit trail, scheduled autonomous reviews, and a browser UI
(FastAPI + HTMX + vanilla JS, Pydantic AI, Gemini/Vertex). Pluggable HTTP/PostgreSQL adapters and
inbound signal webhooks make it easy to bolt onto an existing data platform.

> Türkçe dokümantasyon aşağıdadır. Entegrasyon sözleşmeleri: [docs/integration.md](docs/integration.md).

E-ticaret pazaryeri kampanya portföyünü, opencode/Copilot tarzı bir ajanla **tarayıcıdan**
yönetmek için agentic konsol. FastAPI + HTMX + vanilla JS; ajan çekirdeği Pydantic AI;
BigQuery erişimi MCP Toolbox üzerinden; Gemini/Vertex AI ile çalışır.

## Ne yapar?

- **Sohbet ile komut:** "Ev-yaşam kategorisinde 48 saatlik flaş indirim planla", "şu
  kampanyayı durdur", "portföyü incele" gibi talimatları ajan tool çağrılarına çevirir.
- **Guardrail'li otonomi:** `config/policy.yaml` limitlerinin altındaki yazma aksiyonlarını
  ajan kendi uygular; limit üstü aksiyonlar onay kartına düşer, sen onaylayınca kaldığı
  yerden devam eder.
- **Periyodik otonom çalışma:** (opsiyonel scheduler) her sabah günlük portföy taraması,
  haftalık öğrenim derlemesi. Sonuçlar journal'a yazılır, onay gerektirenler kuyruğa düşer.
- **Kalıcı bellek:** `skills/*/SKILL.md` prosedürleri, `data/memory/learnings.md` kalıcı
  öğrenimler, `data/journal/YYYY-MM-DD.md` karar günlüğü, SQLite'da audit trail.
- **Görünürlük:** Tool çağrıları, argümanları ve sonuçları canlı akışta (SSE); Journal ve
  Audit sayfalarında "ajan ne düşündü, ne yaptı" kayıtları.

## Mimari

```
Tarayıcı (HTMX + vanilla JS)
  │  POST mesaj, GET SSE olay akışı, onay butonları
  ▼
FastAPI (app/main.py)
  ├─ API: /api/conversations, /events (SSE), /approvals, /journal, /audit
  ├─ Entegrasyon: /api/signals (dış scheduler'dan sinyal), dış webhook yayını
  ├─ Sayfalar: /, /c/{id}, /journal, /audit
  └─ AgentRunner (app/agent/runner.py)
       ├─ Pydantic AI Agent  ──▶ Gemini / Vertex AI
       │    ├─ Tool'lar: campaign_* , memory_* , (fallback) bigquery_query
       │    ├─ Guardrail'ler: app/agent/guards.py (policy.yaml)
       │    └─ Onay: ApprovalRequired → DeferredToolRequests → onay kartı → resume
       ├─ MCP Toolset ──▶ MCP Toolbox (BigQuery)
       ├─ MarketplaceClient ──▶ mock | HTTP back office | PostgreSQL
       ├─ ReportingClient ──▶ mock | HTTP raporlama | PostgreSQL
       ├─ EventBus (SSE yayını) + dış webhook emitter
       └─ SQLite Store (konuşmalar, run state, onaylar, audit, kampanyalar)

APScheduler: günlük portföy taraması + haftalık öğrenim derlemesi
```

## Hızlı başlangıç

```bash
uv sync
cp .env.example .env
# .env içinde AGENT_MODEL ve GCP ayarlarını düzenle

# Vertex AI için kimlik doğrulama (bir kez):
gcloud auth application-default login

uv run uvicorn app.main:app --reload
# http://127.0.0.1:8000
```

Gemini Developer API (API key) ile hızlı deneme için `.env`:

```
AGENT_MODEL=google-gla:gemini-2.5-flash
GEMINI_API_KEY=...
```

Backend, `MARKETPLACE_API_BASE_URL` boşken **mock modda** çalışır: kampanyalar SQLite'ta
tutulur, performans metrikleri deterministik olarak üretilir. Gerçek API için
`app/backend/marketplace.py` içindeki `HttpMarketplaceClient` endpoint sözleşmesini kendi
API'nize uyarlayın.

## BigQuery (MCP Toolbox)

Google'ın açık kaynak [MCP Toolbox for Databases](https://github.com/googleapis/mcp-toolbox)
sunucusu kullanılır:

```bash
# 1) Toolbox binary'sini indir: https://github.com/googleapis/mcp-toolbox/releases
# 2) Kendi tablolarınız için config/toolbox/tools.yaml.example dosyasını tools.yaml olarak kopyalayıp düzenleyin.
# 3) Çalıştır:
./toolbox --tools-file config/toolbox/tools.yaml --transport http --port 5000
# Generic tool'lar için ekleyin: --prebuilt bigquery
# 4) .env:
TOOLBOX_MCP_URL=http://127.0.0.1:5000/mcp
```

Toolbox çalışmıyorsa ajan otomatik olarak `bigquery_query` **stub** tool'una düşer ve örnek
veri döner (yanıtlarda bunu belirtir). Sunucu portunda başka bir servis varsa (macOS'ta
AirPlay :5000), başlangıçta JSON-RPC initialize ile doğrulama yapılır ve yanlış bağlanma
engellenir.

## Şirket içi entegrasyon

Mevcut çalışan hatlar (BQ scheduler'ı, back office API'si, raporlama servisi, PostgreSQL)
bozulmadan bu konsol karar/onay/gözlem katmanı olarak bağlanır:

- **Sinyal ingestion:** scheduler'ınız `POST /api/signals` (Bearer `INTEGRATION_TOKEN`) ile
  sinyal bırakır → konsol konuşma açar, ajanı tetikler. Toplu: `/api/signals/batch`.
- **Dış webhook'lar:** `EVENT_WEBHOOK_URL` ile `approval_required`, `approval_resolved`,
  `campaign_action`, `run_finished` olayları dashboard'ınıza POST edilir.
- **Backend adaptörleri (env ile seçilir):** kampanya okuma/yazma için
  `MARKETPLACE_API_BASE_URL` veya `POSTGRES_DSN`; analitik için `REPORTING_API_BASE_URL`
  veya `POSTGRES_DSN`; BQ sorguları için MCP Toolbox. Hiçbiri yoksa mock çalışır.
- **Postgres desteği:** `uv sync --extra postgres` + `POSTGRES_DSN`; tablo adları
  `PG_*_TABLE` env'leri ile verilir.
- **Sözleşmeler ve fork-and-adapt adımları:** [docs/integration.md](docs/integration.md).
  OpenAPI: `GET /openapi.json`, arayüz: `GET /docs`.

## Politika ve onay akışı

`config/policy.yaml` — indirim, bütçe, süre, aktif kampanya sayısı, kategori override'ları,
blackout pencereleri, ekonomi hedefleri. Limitler hem system prompt'a enjekte edilir hem de
**tool fonksiyonlarının içinde** zorlanır (prompt'a güvenilmez).

Akış: tool limiti aşınca `ApprovalRequired` fırlatır → run `DeferredToolRequests` ile durur →
UI'da onay kartı çıkar → Onayla/Reddet → run `DeferredToolResults` ile kaldığı yerden devam
eder. Reddedilirse model reddi görür ve alternatif üretir.

## Periyodik otonom çalışma

`.env`:

```
SCHEDULER_ENABLED=true
DAILY_REVIEW_CRON=0 9 * * *
WEEKLY_LEARNING_CRON=30 8 * * 1
```

Günlük iş: portföy taraması → anormallik/fırsat raporu → gerekli aksiyonlar → journal kaydı.
Haftalık iş: audit + journal okuma → kalıcı öğrenim çıkarımı (`save_learning`).

## Geliştirme

```bash
uv run pytest        # testler
uv run ruff check .  # lint
```

- Testler mock model (FunctionModel) kullanır; gerçek LLM çağrısı yapılmaz.
- Anahtar dosyalar: `app/agent/{runner,tools,guards,prompts}.py`, `app/web/`, `app/api/`.
- Detaylar için `AGENTS.md`.

## Güvenlik notları (v1, yerel kullanım)

- Varsayılan olarak yalnızca `127.0.0.1`'de dinleyin; kimlik doğrulama yoktur.
- Onay mekanizması *modelin* izinsiz yazmasını engeller; endpoint güvenliği ve yetki
  kontrolü ayrıca yapılmalıdır. Gerçek yazma yetkisi (API token'ı) tool içinde uygulanır.
- `MARKETPLACE_DRY_RUN=true` iken gerçek backend'e yazma çağrıları `dry_run=true` ile gider.

## Yol haritası fikirleri

- Gerçek pazaryeri API sözleşmesinin `HttpMarketplaceClient`'a işlenmesi
- BQ maliyet kontrolü (query öncesi dry-run, scan limiti)
- Çok kullanıcılı auth + rol bazlı onay yetkisi
- SSE için Redis pub/sub (çok worker), Celery/Arq ile dayanıklı işler
- Sonuç metrikleriyle öğrenim değerlendirme (önerinin gerçekleşen etkisi)

## Lisans

MIT — bkz. [LICENSE](LICENSE).
