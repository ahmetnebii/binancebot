"""Kendi kendini eğiten model: gerçek sinyali bulur, rastgelede bulmaz, gelecek bilgisi kullanmaz."""
import numpy as np

from app.ml import model
from app.ml.features import build_panel

DAY = 86_400_000


def _daily(seed, D=800, n=40, reversal=0.0):
    rng = np.random.default_rng(seed)
    t0 = 1_700_000_000_000 - 1_700_000_000_000 % DAY
    t = t0 + np.arange(D, dtype=np.int64) * DAY
    R = rng.normal(0, 0.04, (D, n))
    if reversal:
        # dün diğerlerinden çok yükselen bugün geri verir (gerçek, öğrenilebilir çapraz kesit etkisi)
        for d in range(1, D):
            prev = R[d - 1] - R[d - 1].mean()
            R[d] += -reversal * prev
    out = {}
    for i in range(n):
        c = 10 * np.exp(np.cumsum(R[:, i]))
        o = np.r_[10, c[:-1]]
        qv = rng.lognormal(16, 0.5, D)
        out[f"C{i}USDT"] = {"t": t, "o": o, "h": np.maximum(o, c) * 1.01, "l": np.minimum(o, c) * 0.99, "c": c,
                            "v": qv / c, "qv": qv, "tbq": qv / 2}
    return out


def test_finds_real_signal():
    r = model.run(build_panel(_daily(1, reversal=0.25), None, min_qv=0))
    assert r["verdict"] in ("recommended", "weak") and r["final"]["apr_pct"] > 20 and r["final"]["t_stat"] > 2
    top = r["importance"][0]["feature"]
    assert top in ("r1", "r3")  # model kısa vadeli getiriyi (geri dönüşü) en önemli özellik olarak öğrenmeli
    assert next(i for i in r["importance"] if i["feature"] == "r1")["coef"] < 0  # yön: geri dönüş


def test_no_false_signal_on_random():
    for seed in (200, 201, 202):
        r = model.run(build_panel(_daily(seed), None, min_qv=0))
        assert r["verdict"] == "none", (seed, r["final"]["t_stat"], r["final"]["apr_pct"])


def test_walk_forward_has_no_lookahead():
    d = _daily(7, D=700)
    p1 = build_panel(d, None, min_qv=0)
    s1, _ = model.walk_forward_scores(p1, 1, 100.0)
    d2 = {k: {kk: (vv.copy() if kk != "c" else vv.copy()) for kk, vv in v.items()} for k, v in d.items()}
    for v in d2.values():
        v["c"][-80:] *= np.linspace(1, 5, 80)  # son 80 günü boz
    p2 = build_panel(d2, None, min_qv=0)
    s2, _ = model.walk_forward_scores(p2, 1, 100.0)
    cut = 700 - 80 - 40  # bozulan bölgeden ve son aydan önce
    a, b = s1[:cut], s2[:cut]
    assert np.allclose(np.nan_to_num(a), np.nan_to_num(b))
