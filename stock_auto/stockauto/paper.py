"""모의 매매(가상 계좌). 실제 주문은 하지 않는다.

- 전략×종목마다 '슬롯'을 하나 두고, 가상 원금 100(지수)에서 시작한다.
- 매일 실행 시: (1) 어제 예약된 주문을 오늘 종가에 체결 → (2) 오늘 종가 기준 신호로 내일 주문 예약.
  백테스트(signals.simulate)와 같은 '신호 다음 거래일 종가 체결' 가정이다.
- 장부(ledger)는 JSON 파일로 저장되어 실행마다 이어진다. 같은 거래일을 두 번 처리하지 않는다.
- 체결가는 종가(close) 기준이며 배당은 반영하지 않는다. 거래비용은 편도 비율로 차감한다.
"""
from __future__ import annotations

import json
import pathlib

import pandas as pd

from . import signals as sg

START_VALUE = 100.0


def load(path: str | pathlib.Path) -> dict:
    p = pathlib.Path(path)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"version": 1, "slots": {}, "history": []}


def save(ledger: dict, path: str | pathlib.Path) -> None:
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")


def _new_slot(strategy: str, name: str, date: str, close: float) -> dict:
    return {
        "strategy": strategy, "name": name, "started": date, "start_price": close,
        "cash": START_VALUE, "shares": 0.0, "entry_price": None, "entry_date": None,
        "entry_value": None, "pending": None, "pending_reason": None,
        "last_bar": None, "last_close": close, "equity": START_VALUE, "trades": [],
    }


def update_slot(slot: dict, px: pd.DataFrame, flows, strat: dict, cost: float) -> list[str]:
    """오늘 거래일 1개를 처리한다. 반환: 오늘 일어난 이벤트 문장 목록."""
    bar = px.index[-1].date().isoformat()
    close = float(px["close"].iloc[-1])
    events: list[str] = []
    if slot["last_bar"] == bar:          # 같은 날 재실행 → 아무것도 하지 않음
        return events
    tag = f"{slot['name']}({slot['strategy']})"

    # 1) 예약 주문 체결 (오늘 종가)
    if slot["pending"] == "buy" and slot["shares"] == 0:
        value = slot["cash"] * (1 - cost)
        slot.update(shares=value / close, cash=0.0, entry_price=close, entry_date=bar,
                    entry_value=slot["cash"])
        events.append(f"{tag} 가상 매수 체결 {close:,.2f}")
    elif slot["pending"] == "sell" and slot["shares"] > 0:
        proceeds = slot["shares"] * close * (1 - cost)
        ret = proceeds / slot["entry_value"] - 1
        slot["trades"].append({"entry_date": slot["entry_date"], "exit_date": bar,
                               "entry": slot["entry_price"], "exit": close,
                               "ret": ret, "reason": slot["pending_reason"]})
        events.append(f"{tag} 가상 매도 체결 {close:,.2f} ({ret*100:+.1f}%, {slot['pending_reason']})")
        slot.update(cash=proceeds, shares=0.0, entry_price=None, entry_date=None, entry_value=None)
    slot["pending"], slot["pending_reason"] = None, None

    # 2) 오늘 종가 기준 신호 → 내일 체결 예약
    buy, sell, _, _ = sg.strategy_signals(px, flows, strat)
    if slot["shares"] == 0:
        if bool(buy.iloc[-1]):
            slot["pending"], slot["pending_reason"] = "buy", "매수 조건"
            events.append(f"{tag} 매수 조건 충족 → 다음 거래일 종가에 가상 매수 예정")
    else:
        ret = close / slot["entry_price"] - 1
        reason = None
        if bool(sell.iloc[-1]):
            reason = "매도 조건"
        elif strat.get("stop_loss") is not None and ret <= strat["stop_loss"]:
            reason = "손절"
        elif strat.get("take_profit") is not None and ret >= strat["take_profit"]:
            reason = "익절"
        if reason:
            slot["pending"], slot["pending_reason"] = "sell", reason
            events.append(f"{tag} {reason} → 다음 거래일 종가에 가상 매도 예정")

    slot["equity"] = slot["cash"] + slot["shares"] * close
    slot["last_bar"], slot["last_close"] = bar, close
    return events


def update(ledger: dict, cfg: dict, prices: dict, flows: dict, asof: str) -> list[str]:
    cost = cfg.get("signal_cost_per_side", 0.0015)
    events: list[str] = []
    for strat in cfg.get("signals", []):
        for name in strat.get("tickers", []):
            px = prices.get(name)
            if px is None or len(px) < 70:
                continue
            key = f"{strat['name']}|{name}"
            if key not in ledger["slots"]:
                ledger["slots"][key] = _new_slot(strat["name"], name, px.index[-1].date().isoformat(),
                                                 float(px["close"].iloc[-1]))
            events += update_slot(ledger["slots"][key], px, flows.get(name), strat, cost)
    # 전략별 평균 평가액 기록 (하루 1건)
    active = {s["name"] for s in cfg.get("signals", [])}
    for strat in active:
        slots = [s for s in ledger["slots"].values() if s["strategy"] == strat]
        if not slots:
            continue
        eq = sum(s["equity"] for s in slots) / len(slots)
        bh = sum(START_VALUE * s["last_close"] / s["start_price"] for s in slots) / len(slots)
        ledger["history"] = [h for h in ledger["history"] if not (h["date"] == asof and h["strategy"] == strat)]
        ledger["history"].append({"date": asof, "strategy": strat, "equity": eq, "buy_hold": bh})
    ledger["history"] = ledger["history"][-2000:]
    prev = ledger.get("last_events", {})
    if not (events == [] and prev.get("date") == asof):   # 같은 날 재실행 시 기존 이벤트 유지
        ledger["last_events"] = {"date": asof, "events": events}
    return events


def summarize(ledger: dict) -> dict:
    rows = []
    for s in ledger["slots"].values():
        t = [x["ret"] for x in s["trades"]]
        state = "보유" if s["shares"] > 0 else "현금"
        if s["pending"] == "buy":
            state = "매수 대기"
        elif s["pending"] == "sell":
            state = f"매도 대기({s['pending_reason']})"
        rows.append({
            "strategy": s["strategy"], "name": s["name"], "started": s["started"], "state": state,
            "entry_date": s["entry_date"], "entry_price": s["entry_price"],
            "return": s["equity"] / START_VALUE - 1,
            "buy_hold": s["last_close"] / s["start_price"] - 1,
            "open_return": (s["last_close"] / s["entry_price"] - 1) if s["shares"] > 0 else None,
            "trades": len(t), "win_rate": (sum(r > 0 for r in t) / len(t)) if t else None,
        })
    trades = sorted(
        ({**x, "name": s["name"], "strategy": s["strategy"]} for s in ledger["slots"].values() for x in s["trades"]),
        key=lambda x: x["exit_date"], reverse=True)[:15]
    by_strat = {}
    for r in rows:
        by_strat.setdefault(r["strategy"], []).append(r)
    strategies = [{"strategy": k, "slots": len(v),
                   "return": sum(r["return"] for r in v) / len(v),
                   "buy_hold": sum(r["buy_hold"] for r in v) / len(v)} for k, v in by_strat.items()]
    return {"rows": rows, "trades": trades, "strategies": strategies,
            "events": ledger.get("last_events", {}).get("events", [])}
