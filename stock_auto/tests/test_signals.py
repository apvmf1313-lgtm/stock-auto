import numpy as np
import pandas as pd
import pytest

from stockauto import signals as sg


def mk(close, n=None):
    idx = pd.bdate_range("2023-01-02", periods=len(close))
    c = pd.Series(close, index=idx, dtype=float)
    return pd.DataFrame({"close": c, "adj_close": c, "volume": 1000.0, "dividend": 0.0})


def test_sma_cross_up_fires_once():
    px = mk([100] * 30 + list(np.linspace(100, 160, 30)))
    s = sg.cond_series({"type": "sma_cross_up", "fast": 5, "slow": 20}, px, None)
    assert s.sum() == 1


def test_rsi_bounds_and_trend():
    up = mk(np.linspace(100, 200, 60))
    r = sg.rsi(up["close"]).dropna()
    assert (r > 95).all()


def test_simulate_no_lookahead_and_next_close_fill():
    # 100일 후 가격이 100 -> 110 으로 점프. 신호는 점프일 종가에 나오고 체결은 다음날 종가.
    close = [100.0] * 101 + [110.0] + [110.0] * 10
    px = mk(close)
    buy = pd.Series(False, index=px.index)
    sell = pd.Series(False, index=px.index)
    buy.iloc[101] = True                      # 점프일 종가에 신호
    r = sg.simulate(px, buy, sell, cost=0.0)
    assert r["return"] == pytest.approx(0.0)  # 점프 이득은 못 먹는다 (다음날 체결)


def test_stop_loss_exits():
    close = [100.0] * 100 + [100.0, 100.0, 90.0, 80.0, 80.0, 80.0, 80.0]
    px = mk(close)
    buy = pd.Series(False, index=px.index); buy.iloc[100] = True
    sell = pd.Series(False, index=px.index)
    r = sg.simulate(px, buy, sell, stop_loss=-0.05, cost=0.0)
    assert r["trades"] == 1
    # 101일 종가 100 체결 → 102일 90(-10%)에서 손절 신호 → 103일 종가 80에 체결 = -20% (다음날 종가 체결 가정)
    assert r["return"] == pytest.approx(-0.20)


def test_cost_reduces_return():
    close = [100.0] * 100 + list(np.linspace(100, 130, 30))
    px = mk(close)
    buy = pd.Series(False, index=px.index); buy.iloc[100] = True
    sell = pd.Series(False, index=px.index); sell.iloc[125] = True
    a = sg.simulate(px, buy, sell, cost=0.0)["return"]
    b = sg.simulate(px, buy, sell, cost=0.005)["return"]
    assert b < a
