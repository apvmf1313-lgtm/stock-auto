import pandas as pd
import pytest

from stockauto import paper

STRAT = {"name": "T", "tickers": ["A"], "buy_all": [{"type": "rsi_below", "n": 14, "value": 101}],
         "sell_any": [], "stop_loss": -0.05}
CFG = {"signals": [STRAT], "signal_cost_per_side": 0.0}


def px_upto(prices, n):
    idx = pd.bdate_range("2024-01-01", periods=len(prices))
    c = pd.Series(prices, index=idx, dtype=float)
    df = pd.DataFrame({"close": c, "adj_close": c, "volume": 1000.0, "dividend": 0.0})
    return df.iloc[:n]


def test_buy_next_day_then_stop_loss():
    # 80일 횡보(RSI 계산용) 후 상승/하락. rsi_below 101 → 항상 매수 조건 참
    prices = [100.0 + (i % 2) for i in range(80)] + [100.0, 110.0, 90.0, 80.0]
    led = {"version": 1, "slots": {}, "history": []}
    paper.update(led, CFG, {"A": px_upto(prices, 80)}, {}, "d0")
    s = led["slots"]["T|A"]
    assert s["pending"] == "buy" and s["shares"] == 0          # 오늘은 예약만
    paper.update(led, CFG, {"A": px_upto(prices, 81)}, {}, "d1")
    assert s["shares"] > 0 and s["entry_price"] == 100.0       # 다음날 종가 체결
    paper.update(led, CFG, {"A": px_upto(prices, 82)}, {}, "d2")
    assert s["pending"] is None                                # +10%, 손절 아님
    paper.update(led, CFG, {"A": px_upto(prices, 83)}, {}, "d3")
    assert s["pending"] == "sell" and s["pending_reason"] == "손절"   # 90 → -10%
    paper.update(led, CFG, {"A": px_upto(prices, 84)}, {}, "d4")
    assert s["shares"] == 0
    assert s["trades"][0]["ret"] == pytest.approx(-0.20)       # 80에 체결
    assert s["equity"] == pytest.approx(80.0)


def test_same_day_rerun_is_idempotent():
    prices = [100.0 + (i % 2) for i in range(82)]
    led = {"version": 1, "slots": {}, "history": []}
    for n in (80, 81, 81, 81):
        paper.update(led, CFG, {"A": px_upto(prices, n)}, {}, f"d{n}")
    s = led["slots"]["T|A"]
    assert s["entry_price"] == prices[80]  # 재실행해도 한 번만 체결
    assert len([h for h in led["history"] if h["date"] == "d81"]) == 1


def test_ledger_roundtrip(tmp_path):
    led = paper.load(tmp_path / "x.json")
    led["slots"]["k"] = {"a": 1}
    paper.save(led, tmp_path / "x.json")
    assert paper.load(tmp_path / "x.json")["slots"]["k"] == {"a": 1}
