"""리포트 가격이 실행 당일 종가까지 포함되는지 (yfinance end 는 미포함이라 하루 더해 요청)."""
import sys
import types

import pandas as pd

from stockauto.providers import LiveProvider, _yf_end


def test_yf_end_adds_one_day():
    assert _yf_end("2026-11-26") == "2026-11-27"
    assert _yf_end("2026-12-31") == "2027-01-01"


def test_prices_requests_through_today(monkeypatch):
    seen = {}

    class FakeTicker:
        def __init__(self, sym):
            pass

        def history(self, start, end, **kw):
            seen["end"] = end
            idx = pd.date_range(start, end, inclusive="left", tz="Asia/Seoul")
            return pd.DataFrame({"Close": 1.0, "Adj Close": 1.0, "Volume": 1, "Dividends": 0.0}, index=idx)

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=FakeTicker))
    px = LiveProvider().prices("005930.KS", "2026-11-20", "2026-11-26")
    assert seen["end"] == "2026-11-27"
    assert px.index[-1] == pd.Timestamp("2026-11-26")
