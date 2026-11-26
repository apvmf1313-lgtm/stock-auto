import pytest

from stockauto import broker as bk
from stockauto.providers import KisProvider

CFG = {
    "watchlist": [{"name": "A", "code": "000001"}, {"name": "B", "code": "000002"}, {"name": "S", "yahoo": "S"}],
    "signals": [{"name": "R1", "tickers": ["A", "B", "S"]}, {"name": "R2", "tickers": ["A"]}],
    "broker": {"capital_krw": 1_000_000, "strategies": ["R1"]},
}


def paper(**pending):
    slots = {}
    for key, side in pending.items():
        slots[key.replace("_", "|")] = {"pending": side, "pending_reason": "조건", "last_bar": "2026-11-26"}
    return {"slots": slots}


class FakeBroker:
    def __init__(self, cash=1_000_000, holdings=None, prices=None, fail=False):
        self.cash, self.h, self.p, self.fail, self.orders = cash, holdings or {}, prices or {}, fail, []

    def balance(self):
        return {"cash": self.cash, "total": self.cash, "pnl": 0, "holdings": self.h}

    def price(self, code):
        return self.p[code]

    def order(self, code, qty, side):
        self.orders.append((code, qty, side))
        return {"ok": not self.fail, "odno": "1", "msg": "거부" if self.fail else "주문 완료"}


def test_plan_only_chosen_strategy_and_codes():
    led = bk.load("/nonexistent")
    acts = bk.plan(CFG, paper(R1_A="buy", R1_S="buy", R2_A="buy"), led)
    assert [(a["key"], a["side"]) for a in acts] == [("R1|A", "buy")]       # R2·코드 없는 종목 제외
    assert acts[0]["budget"] == pytest.approx(500_000)                     # 100만원 / R1 의 코드 있는 종목 2개


def test_sell_before_buy_and_qty_by_budget():
    led = bk.load("/nonexistent")
    led["slots"]["R1|B"] = {"qty": 3}
    fb = FakeBroker(holdings={"000002": {"sellable": 3}}, prices={"000001": 120_000})
    acts = bk.plan(CFG, paper(R1_A="buy", R1_B="sell"), led)
    bk.execute(acts, fb, led, "2026-11-27")
    assert fb.orders == [("000002", 3, "sell"), ("000001", 4, "buy")]       # 50만원 // 12만원 = 4주
    assert led["slots"]["R1|A"]["qty"] == 4 and led["slots"]["R1|B"]["qty"] == 0


def test_idempotent_same_day():
    led = bk.load("/nonexistent")
    fb = FakeBroker(prices={"000001": 100_000})
    p = paper(R1_A="buy")
    bk.execute(bk.plan(CFG, p, led), fb, led, "d")
    bk.execute(bk.plan(CFG, p, led), fb, led, "d")
    assert len(fb.orders) == 1


def test_too_expensive_is_skipped_not_ordered():
    led = bk.load("/nonexistent")
    fb = FakeBroker(prices={"000001": 900_000})
    msgs = bk.execute(bk.plan(CFG, paper(R1_A="buy"), led), fb, led, "d")
    assert fb.orders == [] and "건너뜀" in msgs[0]


def test_failed_order_is_retried_next_time():
    led = bk.load("/nonexistent")
    fb = FakeBroker(prices={"000001": 100_000}, fail=True)
    bk.execute(bk.plan(CFG, paper(R1_A="buy"), led), fb, led, "d")
    assert "R1|A" not in led["done"]                                       # 실패는 완료로 기록하지 않음
    assert bk.plan(CFG, paper(R1_A="buy"), led)


def test_refuses_real_server():
    kis = KisProvider("k", "s", env="real", session=object())
    with pytest.raises(RuntimeError):
        bk.MockBroker(kis, "50225326")


def test_parse_account():
    assert bk.parse_account("50225326") == ("50225326", "01")
    assert bk.parse_account("50225326-01") == ("50225326", "01")
