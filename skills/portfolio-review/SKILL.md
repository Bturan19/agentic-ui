---
name: portfolio-review
description: Portföy taraması prosedürü — performans, anormallik ve fırsat tespiti adımları
---

# Portföy Taraması Prosedürü

## 1. Veriyi topla
- `portfolio_summary(days=7)` ile portföyün tamamını çek.
- Şüpheli kampanyalar için `get_campaign_performance(campaign_id, days=14)` ile derinleş.
- BigQuery tool'ları bağlıysa kategori/ürün kırılımını da çek.

## 2. Anormallik ve fırsat tespiti
- ROAS < 1 veya marj < %5: riskli — duraklatma/optimizasyon düşün.
- ROAS > hedef ve stok yeterli: fırsat — bütçe artışı veya süre uzatma düşün.
- Bir kampanyada düşüş varsa iki haftalık trende bak, tek güne bakıp karar verme.

## 3. Öneri formatı
Her öneri üç satır olmalı:
- **Aksiyon:** ne yapılacak (kampanya, ürün seti, indirim, süre, bütçe)
- **Gerekçe:** hangi veriye dayanıyor (sayıyla)
- **Beklenen etki:** ciro ve marj tahmini, ROAS

## 4. Uygulama
- Limit içindeki aksiyonları tool ile doğrudan uygula.
- Limit üstü aksiyonlarda tool'u çağır; onay kuyruğuna düşmesi normaldir.
- Sonunda `write_journal` ile özet yaz.
