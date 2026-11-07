"""Tek tuşla tam analiz: adım planı ve final raporu.

Ana iş sırasıyla şunları çalıştırır (her biri kendi sekmesinde de görünen normal bir iştir):
veri güncelleme → strateji laboratuvarı (coin grupları + coin bazlı) → funding arbitrajı (3 evren)
→ kalabalık analizi. Sonunda tüm adaylar TEK ölçüyle karşılaştırılır: aramada hiç kullanılmamış
dönemdeki (final sınavı / test dönemi) yıllıklandırılmış getiri.
"""
from __future__ import annotations

from typing import Any, Optional

DAY = 86_400_000

# (anahtar, ad, ağırlık %, iş türü, gövde)
STEPS: list[tuple[str, str, float, str, dict[str, Any]]] = [
    ("data", "Geçmiş veriler (3 yıl · ilk 30 coin)", 8, "data", {"days": 1095, "top_n": 30}),
    ("lab_groups", "Strateji laboratuvarı · coin grupları (30.000 kural × 3 grup)", 40, "lab",
     {"days": 1095, "top_n": 30, "budget": "derin", "mode": "groups"}),
    ("lab_coins", "Strateji laboratuvarı · coin bazlı (ilk 10 coin × 10.000 kural)", 34, "lab",
     {"days": 1095, "top_n": 10, "budget": "normal", "mode": "coins"}),
    ("carry_binance", "Funding arbitrajı · Binance spot, likit coinler", 4, "carry",
     {"days": 1095, "top_n": 60, "spot_fee": 0.00075, "leverage": 3, "universe": "binance"}),
    ("carry_all", "Funding arbitrajı · Binance spot, tüm coinler", 5, "carry",
     {"days": 1095, "top_n": 1000, "spot_fee": 0.00075, "leverage": 3, "universe": "all"}),
    ("carry_cross", "Funding arbitrajı · çapraz borsa (Bybit / OKX / Gate spot)", 5, "carry",
     {"days": 1095, "top_n": 1000, "spot_fee": 0.001, "leverage": 3, "universe": "cross"}),
    ("crowd", "Kalabalık verisi analizi", 4, "crowd", {}),
]

VERDICT_RANK = {"recommended": 3, "weak": 2}


def _month(ts_ms: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m")


def lab_monthly(res: dict, group: dict) -> list[dict]:
    """Strateji adayının hiç görülmemiş (final sınavı) dönemdeki aylık getirileri (%)."""
    full = group.get("full") or {}
    months = full.get("monthly") or []
    start = (res.get("period") or {}).get("split2")
    if not start:
        return []
    m0 = _month(int(start))
    return [{"month": m["month"], "return_pct": float(m["return_pct"])} for m in months if m["month"] >= m0]


def carry_monthly(res: dict) -> list[dict]:
    return [{"month": m["month"], "return_pct": float(m["return_pct"])} for m in (res.get("portfolio") or {}).get("monthly_test") or []]


def candidate_monthly(c: dict, res: Optional[dict]) -> list[dict]:
    if not res:
        return []
    if c["kind"] == "carry":
        return carry_monthly(res)
    key = c.get("group")
    name = c["title"].replace("Strateji · ", "")
    for g in res.get("groups") or []:
        if (key and g.get("key") == key) or (not key and g.get("name") == name):
            return lab_monthly(res, g)
    return []


def _lab_candidates(step_key: str, job_id: str, res: dict) -> list[dict]:
    out = []
    period = res.get("period") or {}
    final_days = max(1.0, ((period.get("end") or 0) - (period.get("split2") or 0)) / DAY)
    for g in res.get("groups") or []:
        b = g.get("best")
        if not b or not b.get("final"):
            continue
        f = b["final"]
        ret = float(f.get("return_pct") or 0.0)
        out.append({
            "kind": "lab", "step": step_key, "job_id": job_id, "group": g.get("key"), "binance_only": True,
            "monthly": lab_monthly(res, g),
            "title": f"Strateji · {g.get('name')}",
            "label": b.get("label"), "details": b.get("details", []),
            "verdict": g.get("verdict", "none"), "forward_ok": bool(g.get("forward_ok")),
            "oos_return_pct": ret, "oos_days": final_days, "oos_apr_pct": ret / final_days * 365.0,
            "oos_max_dd_pct": f.get("max_drawdown_pct"), "oos_trades": f.get("trades"),
            "oos_expectancy_r": f.get("expectancy_r"),
            "full_return_pct": (g.get("full_summary") or {}).get("total_return_pct"),
            "how": "Kuralı canlı taramaya uygular (S6). Önce Simülasyon'da dene.",
        })
    return out


def _carry_candidate(step_key: str, title: str, job_id: str, res: dict) -> Optional[dict]:
    p = res.get("portfolio")
    if not p:
        return None
    t = p["test"]
    pr = p["params"]
    days = max(1.0, ((res.get("period") or {}).get("end", 0) - (res.get("period") or {}).get("split", 0)) / DAY)
    return {
        "kind": "carry", "step": step_key, "job_id": job_id, "title": title, "monthly": carry_monthly(res),
        "binance_only": res.get("universe", "binance") != "cross",
        "label": f"İlk {pr['top_n']} coin · son {pr['k']} dönem ort. yıllık %{pr['enter_apr']:g} üstü",
        "details": [
            f"{res.get('coins_used', len(res.get('coins', [])))} coin analiz edildi"
            + (f" ({res.get('coins_total')} coinden; spotu olmayanlar çıkarıldı)" if res.get("coins_total") else ""),
            f"Test dönemi: en büyük düşüş %{t['max_drawdown_pct']:.2f}, artı ay oranı %{(t.get('positive_months_pct') or 0):.0f}, "
            f"pozisyonda kalma %{t['time_in_pct']:.0f}",
            f"Gidiş-dönüş maliyet %{res['costs']['round_trip_pct']:.2f}, {res['costs']['leverage']:g}x",
        ],
        "verdict": res.get("verdict", "none"), "forward_ok": False,
        "oos_return_pct": t["return_pct"], "oos_days": days, "oos_apr_pct": t["apr_pct"],
        "oos_max_dd_pct": t["max_drawdown_pct"], "oos_trades": len(p["trades_test"]) if "trades_test" in p else None,
        "oos_expectancy_r": None,
        "full_return_pct": None,
        "how": "Kuralı canlı funding radarına ve sanal portföye bağlar. Gerçekte uygulama adımları Funding sekmesinde.",
    }


def rank_key(c: dict) -> tuple:
    return (VERDICT_RANK.get(c["verdict"], 1 if c.get("forward_ok") else 0), c["oos_apr_pct"])


def build_report(steps: list[dict], step_results: dict[str, Optional[dict]], crowd: Optional[dict]) -> dict:
    """steps: [{key, name, job_id, status}], step_results: anahtar → iş sonucu (result) ya da None."""
    cands: list[dict] = []
    for st in steps:
        res = step_results.get(st["key"])
        if not res:
            continue
        if st["key"].startswith("lab"):
            cands += _lab_candidates(st["key"], st.get("job_id"), res)
        elif st["key"].startswith("carry"):
            c = _carry_candidate(st["key"], st["name"].replace("Funding arbitrajı · ", "Funding · "), st.get("job_id"), res)
            if c:
                cands.append(c)
    cands.sort(key=rank_key, reverse=True)
    best = next((c for c in cands if c["verdict"] in VERDICT_RANK and c["oos_apr_pct"] > 0), None)
    trial = next((c for c in cands if c is not best and c["oos_apr_pct"] > 0 and (c.get("forward_ok") or c["verdict"] in VERDICT_RANK)), None)
    strong_crowd = []
    if crowd and crowd.get("enough"):
        strong_crowd = [r for r in crowd.get("results", []) if r.get("diff_t") is not None and abs(r["diff_t"]) >= 3]
    if best:
        summary = (f"En iyi sonuç: {best['title']} — hiç görülmemiş dönemde yıllık %{best['oos_apr_pct']:.1f}. "
                   "Bu, binlerce denemenin en iyisi olduğu için gerçekte biraz daha düşük beklenmeli; önce Simülasyon'da dene.")
    elif trial:
        summary = (f"Doğrulanmış bir avantaj bulunamadı. En umut verici aday: {trial['title']} "
                   f"(hiç görülmemiş dönemde yıllık %{trial['oos_apr_pct']:.1f}); yalnızca Simülasyon'da denemeye değer.")
    else:
        summary = ("Hiçbir yöntem hiç görülmemiş dönemde anlamlı kâr etmedi. Bu verilerde gerçek bir avantaj yok; "
                   "gerçek parayla otomatik işlem önerilmez. Bot açık kaldıkça radar ve kalabalık verisi fırsat arar.")
    return {
        "summary": summary,
        "best": best,
        "trial": trial,
        "candidates": cands,
        "crowd": {"days": (crowd or {}).get("days"), "enough": (crowd or {}).get("enough"), "strong": strong_crowd,
                  "note": (crowd or {}).get("note")},
        "warnings": [
            "Tüm adaylar aynı ölçüyle karşılaştırıldı: aramada hiç kullanılmamış dönemdeki getiri (yıllığa çevrilmiş).",
            "Strateji adaylarının final sınavı ~7 ay, funding adaylarının test dönemi ~14 aydır; kısa dönem daha belirsizdir.",
            "Çok sayıda aday arasından en iyisini seçmek sonuca iyimserlik katar. Gerçek parayla başlamadan önce Simülasyon'da izleyin.",
        ],
    }
