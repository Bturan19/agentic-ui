from __future__ import annotations

import json


def signal_prompt(signal: dict) -> str:
    """Dis sistemden gelen sinyali ajan gorevine cevirir."""
    payload = json.dumps(signal.get("payload") or {}, ensure_ascii=False, indent=2)
    proposed = signal.get("proposed_action")
    proposed_text = json.dumps(proposed, ensure_ascii=False, indent=2) if proposed else "(yok)"
    return f"""\
Dis sistemden bir kampanya sinyali alindi. Sinyal isleme modundasin.

Sinyal ID: {signal.get("signal_id") or "-"}
Case: {signal.get("case")}
Kaynak: {signal.get("source") or "-"}
Gerekce: {signal.get("rationale") or "-"}

Payload:
{payload}

Onerilen aksiyon (varsa, baglayici degil):
{proposed_text}

Gorev:
1. Sinyali dogrula: ilgili kampanya/veri icin ugun tool'larla (performans, kategori, BQ)
   teyit et. Sinyal verisiyle celisen bir durum varsa bunu acikca belirt.
2. Kampanya onerisi uygunsa propose_campaign cagir; limit ustu ise sistem onay ister.
   Uygun degilse nedenini ve alternatifini yaz, aksiyon alma.
3. Kisa bir degerlendirme yaz (dogrulama sonucu, beklenen etki, yaptigin aksiyon).
4. write_journal ile ozet kaydet; kalici bir ogrenim varsa save_learning kullan.

Tum yanitlar Turkce olmali."""
