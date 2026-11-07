"""Geçmiş veri önbelleği: yeni listelenmiş coin her seferinde baştan indirilmemeli; arama ağa çıkmamalı."""
import asyncio

import numpy as np

from app.backtest.data import STEP, BinanceHistory, _rows_to_arrays

DAY = 86_400_000


class FakeHistory(BinanceHistory):
    def __init__(self, cache_dir, listed_at):
        super().__init__("http://x", cache_dir)
        self.listed_at = listed_at
        self.calls: list[tuple[int, int]] = []
        self.fund_calls = 0

    async def _fetch_klines(self, session, symbol, start, end):
        self.calls.append((start, end))
        s = max(start, self.listed_at)
        s -= s % STEP
        ts = np.arange(s, end, STEP)
        rows = [[int(t), "1", "1.1", "0.9", "1", "10", int(t) + STEP - 1, "10", 1, "5", "5", "0"] for t in ts]
        return _rows_to_arrays(rows)

    async def _get(self, session, path, params, weight):
        self.fund_calls += 1
        st = max(params["startTime"], self.listed_at)
        st = st - st % (8 * 3_600_000) + 8 * 3_600_000
        return [{"fundingTime": t, "fundingRate": "0.0001"} for t in range(st, params["endTime"], 8 * 3_600_000)][:1000]


def _load(h, start, end, update_tail):
    return asyncio.run(h.load(["NEWUSDT"], start, end, lambda f, m: None, lambda: False, update_tail=update_tail))


def test_new_coin_not_redownloaded(tmp_path):
    end = 1_800_000_000_000 - (1_800_000_000_000 % STEP)
    start = end - 1095 * DAY
    h = FakeHistory(tmp_path, listed_at=end - 200 * DAY)  # 3 yıldan yeni listelenmiş coin
    d1 = _load(h, start, end, True)
    assert len(h.calls) == 1 and len(d1.bars["NEWUSDT"]["t"]) == 200 * 96
    fund1 = h.fund_calls
    # aynı dönem: ağa hiç çıkılmaz (eskiden her seferinde baştan indiriliyordu)
    h2 = FakeHistory(tmp_path, listed_at=end - 200 * DAY)
    d2 = _load(h2, start, end, False)
    assert h2.calls == [] and h2.fund_calls == 0
    assert np.array_equal(d2.bars["NEWUSDT"]["t"], d1.bars["NEWUSDT"]["t"])
    assert fund1 > 0


def test_tail_update_fetches_only_new_bars(tmp_path):
    end = 1_800_000_000_000 - (1_800_000_000_000 % STEP)
    start = end - 400 * DAY
    h = FakeHistory(tmp_path, listed_at=0)
    _load(h, start, end, True)
    later = end + 2 * DAY
    h2 = FakeHistory(tmp_path, listed_at=0)
    d = _load(h2, start + 2 * DAY, later, True)
    assert len(h2.calls) == 1 and h2.calls[0][0] == end  # yalnızca yeni 2 gün
    assert int(d.bars["NEWUSDT"]["t"][-1]) == later - STEP
    # strateji araması kipinde (update_tail=False) eski önbellek ağa çıkmadan kullanılır
    h3 = FakeHistory(tmp_path, listed_at=0)
    _load(h3, start, later + 5 * DAY, False)
    assert h3.calls == [] and h3.fund_calls == 0


def test_cache_status(tmp_path):
    end = 1_800_000_000_000 - (1_800_000_000_000 % STEP)
    h = FakeHistory(tmp_path, listed_at=0)
    _load(h, end - 30 * DAY, end, True)
    st = h.cache_status()
    assert st["symbols"] == 1 and st["last"] == end and st["first"] == end - 30 * DAY and st["size_mb"] >= 0


def test_funding_only_skips_candles(tmp_path):
    end = 1_800_000_000_000 - (1_800_000_000_000 % STEP)
    h = FakeHistory(tmp_path, listed_at=0)
    d = asyncio.run(h.load(["NEWUSDT", "OTHERUSDT"], end - 400 * DAY, end, lambda f, m: None, lambda: False, funding_only=True))
    assert h.calls == [] and h.fund_calls > 0  # mum indirilmez, yalnızca funding
    assert not d.bars and set(d.funding) == {"NEWUSDT", "OTHERUSDT"} and len(d.funding["NEWUSDT"][0]) > 1000
