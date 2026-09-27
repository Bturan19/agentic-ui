# Entegrasyon Rehberi

Bu doküman, mevcut pazaryeri sistemleriyle (BQ raporlama, back office kampanya API'si,
PostgreSQL, 30 dakikalık sinyal scheduler'ı) entegrasyon sözleşmesini tanımlar.
Hedef: mevcut çalışan hatları bozmadan, bu konsolu karar/onay/gözlem katmanı olarak
bağlamak.

## Katman modeli

```
[Veri düzlemi — sirket ici, mevcut kalir]
  BQ sorgulari / scheduler (30 dk)  ──►  sinyaller  ──►  deterministik filtreler
  Back office kampanya API'si (ac/durdur/butce)
  Dashboard (canli izleme)

[Kontrol duzlemi — bu konsol]
  Sinyal ingestion  →  ajan dogrulamasi (BQ/raporlama tool'lari)
  Policy guardrail  →  limit ustu aksiyonlarda INSAN onayi (pause/resume)
  Audit + journal + ogrenim + skill'ler
  Konusmali inceleme ve canli akis (SSE)
```

## 1. Sinyal ingestion (dis sistem → konsol)

Scheduler'ınız ürettiği sinyalleri bu endpoint'e bırakır; konsol her sinyal için bir
konuşma açar ve ajanı tetikler.

```bash
curl -X POST http://KONSOL/api/signals \
  -H "Authorization: Bearer $INTEGRATION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "case": "flash-indirim-firsati",
    "signal_id": "SIG-2026-1001",
    "title": "Ev-Yaşam flaş fırsatı",
    "payload": {"category": "ev-yasam", "roas": 2.4, "stok_gunu": 9},
    "proposed_action": {"type": "campaign", "discount_pct": 20, "budget": 40000},
    "rationale": "Kategori ROAS ortalamanın üzerinde",
    "source": "bq-scheduler",
    "auto_run": true
  }'
```

Yanıt (202):

```json
{"conversation_id": "conv_ab12cd34ef56", "run_id": "run_99aa88bb77cc", "url": "/c/conv_ab12cd34ef56"}
```

| Alan | Zorunlu | Açıklama |
|---|---|---|
| `case` | evet | Sinyal tipi (audit'te `name` olarak görünür) |
| `signal_id` | hayır | Sizin taraftaki kimlik (izlenebilirlik) |
| `title` | hayır | Konuşma başlığı (yoksa `Sinyal: <case>`) |
| `payload` | hayır | Ham sinyal verisi (ajana JSON olarak verilir) |
| `proposed_action` | hayır | Öneriniz; ajan bağlayıcı olmadan değerlendirir |
| `rationale` | hayır | Gerekçe metni |
| `source` | hayır | Kaynak sistem adı |
| `auto_run` | hayır (true) | false ise sadece kayıt+açılır, ajan çalışmaz (elle tetikleme) |

Toplu gönderim: `POST /api/signals/batch` gövdesi `SignalIn[]`, yanıtı
`{"accepted": n, "signals": [...]}`.

Kimlik doğrulama: `Authorization: Bearer <INTEGRATION_TOKEN>` veya `X-Integration-Token`.
`INTEGRATION_TOKEN` boşsa endpoint 503 döner (varsayılan kapalı).

Ajanın sinyal için izlediği akış: doğrula (raporlama/BQ tool'ları) → gerekiyorsa
`propose_campaign` çağır (limit üstüyse onaya düşer) → değerlendirme yaz → `write_journal`.

## 2. Dış webhook'lar (konsol → dis sistem)

`EVENT_WEBHOOK_URL` ayarlıysa konsol şu olayları POST eder
(`Authorization: Bearer <INTEGRATION_TOKEN>`):

| Event | Ne zaman | Önemli alanlar |
|---|---|---|
| `approval_required` | Limit üstü aksiyon onaya düştü | `approval_id`, `tool_name`, `args`, `reason` |
| `approval_resolved` | İnsan onayladı/reddetti | `approval_id`, `status`, `note` |
| `campaign_action` | Yazma tool'u başarıyla çalıştı | `action`, `result.campaign` |
| `run_finished` | Run bitti | `status` (`done`/`waiting_approval`/`error`), `trigger` |

Örnek gövde:

```json
{
  "event": "campaign_action",
  "ts": "2026-09-27T19:21:18+00:00",
  "conversation_id": "conv_ab12",
  "run_id": "run_99aa",
  "action": "propose_campaign",
  "result": {"status": "created", "campaign": {"id": "CMP-A1B2C3D4", "budget": 40000}}
}
```

Webhook hataları loglanır, run'ı etkilemez. İdempotens için `event + run_id + approval_id`
kullanabilirsiniz.

## 3. Backend adaptörleri

Üç katman birbirinden bağımsız seçilir (env ile):

| Katman | Amaç | Seçim |
|---|---|---|
| Marketplace | Kampanya okuma/yazma (back office) | `MARKETPLACE_API_BASE_URL` → HTTP; `POSTGRES_DSN` → Postgres; ikisi de yoksa mock |
| Reporting | Performans/portföy/kategori analitiği | `REPORTING_API_BASE_URL` → HTTP; `POSTGRES_DSN` → Postgres; yoksa mock |
| BQ sorguları | Ajanın serbest/parametreli BQ erişimi | `TOOLBOX_MCP_URL` → MCP Toolbox; yoksa stub |

### 3.1 Marketplace HTTP sözleşmesi

`app/backend/marketplace.py` → `HttpMarketplaceClient`. Kendi API'nize göre endpoint
yollarını burada uyarlayın.

| Bizim çağrı | HTTP | Gövde/Parametre | Beklenen yanıt |
|---|---|---|---|
| `list_campaigns(status)` | `GET /campaigns` | `status` | `[{campaign}]` |
| `get_campaign(id)` | `GET /campaigns/{id}` | - | `{campaign}` / 404 |
| `count_active()` | `GET /campaigns` | `status=active&count_only=true` | `{"count": n}` veya liste |
| `create_campaign(draft)` | `POST /campaigns` | draft JSON + `dry_run` | `{campaign}` |
| `update_budget(id, v)` | `PATCH /campaigns/{id}` | `{budget, note}` + `dry_run` | `{campaign}` |
| `set_status(id, s)` | `POST /campaigns/{id}/status` | `{status, note}` + `dry_run` | `{campaign}` |

`campaign` şekli: `{id, name, category, status, discount_pct, budget, starts_at, ends_at,
product_ids[], notes}`. `MARKETPLACE_DRY_RUN=true` iken yazma çağrıları
`?dry_run=true` ile gider.

### 3.2 Raporlama HTTP sözleşmesi

`app/backend/reporting.py` → `HttpReportingClient`.

| Endpoint | Parametre | Beklenen yanıt (önerilen şekil) |
|---|---|---|
| `GET /reporting/campaign-performance` | `campaign_id, days` | `{campaign, days, series: [{date, impressions, clicks, orders, revenue, spend, margin}], totals}` |
| `GET /reporting/portfolio-summary` | `days` | `{days, totals, active_campaign_count, top[], bottom[], anomalies[], yesterday_revenue, previous_avg_revenue}` |
| `GET /reporting/category-product-performance` | `category, days, min_revenue` | `{category, days, product_count, products: [{product_id, product_name, revenue, orders, margin, margin_pct, spend, roas}], totals}` |

Mock implementasyonun çıktısı referans şekildir (`app/backend/reporting.py`).

### 3.3 PostgreSQL adaptörü

`uv sync --extra postgres` (asyncpg) ve `.env`:

```
POSTGRES_DSN=postgresql://user:pass@host:5432/marketplace
PG_CAMPAIGNS_TABLE=campaigns
PG_CAMPAIGN_DAILY_TABLE=campaign_daily
PG_CATEGORY_DAILY_TABLE=category_product_daily
```

Beklenen şema (kolon adları sabittir; gerekirse `app/backend/postgres.py` içindeki SQL'i
düzenleyin — tablo adları env'den gelir, SQL injection'a karşı doğrulanır):

```sql
campaigns(id text pk, name text, category text, status text, discount_pct numeric,
          budget numeric, starts_at date, ends_at date, product_ids text[],
          notes text, created_at timestamptz, updated_at timestamptz)

campaign_daily(campaign_id text, date date, revenue numeric, orders int,
               spend numeric, margin numeric, impressions int, clicks int)

category_product_daily(category text, product_id text, product_name text, date date,
                       revenue numeric, orders int, margin numeric, spend numeric)
```

### 3.4 BQ sorgularınızı MCP Toolbox'a taşıma

Mevcut SQL'leriniz değişmeden `config/toolbox/tools.yaml` içine `bigquery-sql` tool'u
olarak taşınır (parametreli, güvenli). Örnek: `config/toolbox/tools.yaml.example`.
Adımlar:

1. `tools.yaml` içinde `kind: source` + `bigquery` kaynağı tanımlayın.
2. Her case sorgunuzu `kind: tool`, `type: bigquery-sql` olarak ekleyin (aynı SQL).
3. Toolbox'ı HTTP modunda çalıştırın, `TOOLBOX_MCP_URL` verin.
4. Ajan bu tool'ları otomatik görür; guardrail ve audit devrede kalır.

## 4. Fork-and-adapt kontrol listesi (coding agent için)

1. `uv sync` → `uv run pytest` (39 test, mock model — API anahtarı gerekmez).
2. `.env`: `AGENT_MODEL`, `GOOGLE_CLOUD_*`; gerekiyorsa `MARKETPLACE_API_BASE_URL`,
   `REPORTING_API_BASE_URL`, `POSTGRES_DSN`, `TOOLBOX_MCP_URL`, `INTEGRATION_TOKEN`,
   `EVENT_WEBHOOK_URL`.
3. Back office endpoint'lerinizi `app/backend/marketplace.py:HttpMarketplaceClient`
   içinde eşleyin (yalnızca bu dosya + gerekirse `reporting.py`).
4. Mevcut BQ SQL'lerini `config/toolbox/tools.yaml`'a taşıyın.
5. Kampanya domain kurallarınızı `config/policy.yaml` ve şirket prosedürlerinizi
   `skills/*/SKILL.md` dosyalarına yazın.
6. Ajan personasını `config/agent.md` içinde güncelleyin (restart gerekmez).
7. Scheduler'ınızdan `POST /api/signals` çağırın; onay/aksiyon olaylarını
   `EVENT_WEBHOOK_URL` ile dashboard'ınıza alın.
8. OpenAPI: `GET /openapi.json`, arayüz: `GET /docs`.

## 5. Güvenlik

- Konsol varsayılan olarak yalnızca `127.0.0.1` dinler ve kullanıcı auth'u yoktur;
  şirket ağına açarken reverse proxy + kimlik doğrulama ekleyin (yol haritasında).
- `INTEGRATION_TOKEN` güçlü seçilmeli; sinyal endpoint'i yalnızca bu token ile açılır.
- Onay mekanizması modeli dizginler; yetkilendirme sınırı değildir. Gerçek yazma yetkisi
  back office API token'ındadır ve `dry_run` ile güvenli test edilebilir.
- Guardrail'ler tool içinde çalışır; prompt manipülasyonu limitleri aşamaz.
