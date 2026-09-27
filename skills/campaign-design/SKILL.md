---
name: campaign-design
description: Kampanya tasarım rehberi — hedef kitle, kategori/ürün seçimi, indirim derinliği ve süre kararı
---

# Kampanya Tasarım Rehberi

## Hedef belirleme
- Amaç ciro mu, marj mı, stok eritme mi? Önce bunu netleştir.
- Mass kullanıcı kampanyalarında indirim derinliği düşük, hacim yüksek olur.
- Segment kampanyalarında (ör. ev-yaşam, yeni müşteri) kişiselleştirme öne çıkar.

## Ürün seçimi
- Son 30 günün ciro/marj verisine bak; yüksek marj + orta dönüşüm ürünleri seç.
- Stok fazlası olan ve marjı yeterli ürünler flaş indirim için idealdir.
- Marjı %10'un altındaki ürünlerde indirim yerine paket/hediye kurgusu düşün.

## İndirim ve süre
- Flaş indirim: kısa süre (24-72 saat), derin indirim, ana sayfa görünürlüğü.
- Hafta sonu kampanyası: orta indirim, 2-4 gün, geniş kategori.
- Kategori override limitlerini `get_policy` ile kontrol et (ör. elektronik %15).

## Öneri çıktısı
`propose_campaign` çağrısında şu alanları eksiksiz doldur:
- `name`, `category`, `discount_pct`, `budget`, `starts_at`, `ends_at`,
  `product_ids`, `rationale` (niceliksel gerekçe).
- Süre ve bütçe seçimini bir cümleyle açıkla; limit üstüyse sorun değil,
  onay kuyruğuna düşer.
