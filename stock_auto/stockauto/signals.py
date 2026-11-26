"""사용자 정의 규칙 기반 매수·매도 '알림' 엔진.

- 규칙은 config.yaml 의 signals 에 사용자가 직접 정의한다 (기본값은 동작 예시일 뿐 추천이 아님).
- 같은 규칙을 과거 데이터에 돌려 성과를 함께 보여준다. 과거 성과는 미래를 보장하지 않는다.
- 체결 가정: 종가에 신호가 나면 '다음 거래일 종가'에 체결 (룩어헤드 방지), 거래비용 반영.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ---------- 지표 ----------
def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def smart_ratio(px: pd.DataFrame, flows: pd.DataFrame | None, w: int) -> pd.Series:
    if flows is None or flows.empty:
        return pd.Series(np.nan, index=px.index)
    net = (flows["foreign"] + flows["institution"]).reindex(px.index)
    traded = px["close"] * px["volume"]
    return net.rolling(w).sum() / traded.rolling(w).sum()


# ---------- 조건 ----------
def cond_series(c: dict, px: pd.DataFrame, flows: pd.DataFrame | None) -> pd.Series:
    t = c["type"]
    close = px["close"]
    if t in ("sma_cross_up", "sma_cross_down", "sma_above", "sma_below"):
        f, s = sma(close, c["fast"]), sma(close, c["slow"])
        if t == "sma_cross_up":
            return (f > s) & (f.shift() <= s.shift())
        if t == "sma_cross_down":
            return (f < s) & (f.shift() >= s.shift())
        return (f > s) if t == "sma_above" else (f < s)
    if t == "rsi_below":
        return rsi(close, c.get("n", 14)) < c["value"]
    if t == "rsi_above":
        return rsi(close, c.get("n", 14)) > c["value"]
    if t in ("smart_ratio_above", "smart_ratio_below"):
        r = smart_ratio(px, flows, c.get("window", 20))
        return (r > c["value"]) if t.endswith("above") else (r < c["value"])
    raise ValueError(f"알 수 없는 조건 type: {t}")


def label(c: dict) -> str:
    t = c["type"]
    if t.startswith("sma_"):
        return f"{t} {c['fast']}/{c['slow']}"
    if t.startswith("rsi"):
        return f"{t} {c.get('n', 14)}일 {c['value']}"
    return f"{t} {c.get('window', 20)}일 {c['value']}"


def strategy_signals(px, flows, strat):
    buy_parts = [cond_series(c, px, flows).fillna(False) for c in strat["buy_all"]]
    sell_parts = [cond_series(c, px, flows).fillna(False) for c in strat.get("sell_any", [])]
    buy = pd.concat(buy_parts, axis=1).all(axis=1) if buy_parts else pd.Series(False, index=px.index)
    sell = pd.concat(sell_parts, axis=1).any(axis=1) if sell_parts else pd.Series(False, index=px.index)
    return buy, sell, buy_parts, sell_parts


# ---------- 시뮬레이션 ----------
def simulate(px: pd.DataFrame, buy: pd.Series, sell: pd.Series, stop_loss=None, take_profit=None,
             cost: float = 0.0015, warmup: int = 100) -> dict:
    """신호(종가) → 다음 거래일 종가 체결. 수익은 adj_close(배당 재투자) 기준. cost 는 편도 비율."""
    adj = px["adj_close"].to_numpy()
    n = len(adj)
    buy_a, sell_a = buy.to_numpy(), sell.to_numpy()
    eq = np.ones(n)
    pos, pending, entry = False, None, None
    trades: list[float] = []
    for i in range(warmup, n):
        eq[i] = eq[i - 1]
        if pos:
            eq[i] *= adj[i] / adj[i - 1]
        # 어제 종가에 난 신호를 오늘 종가에 체결
        if pending == "buy" and not pos:
            eq[i] *= 1 - cost
            pos, entry = True, adj[i]
        elif pending == "sell" and pos:
            eq[i] *= 1 - cost
            trades.append(adj[i] / entry - 1 - 2 * cost)
            pos, entry = False, None
        pending = None
        # 오늘 종가 기준 신호 → 내일 체결 예약
        if not pos and buy_a[i]:
            pending = "buy"
        elif pos:
            ret = adj[i] / entry - 1
            if sell_a[i] or (stop_loss is not None and ret <= stop_loss) or (
                    take_profit is not None and ret >= take_profit):
                pending = "sell"
    eq_s = pd.Series(eq, index=px.index).iloc[warmup:]
    bh = adj[warmup:] / adj[warmup] if n > warmup else np.array([1.0])
    dd = eq_s / eq_s.cummax() - 1
    t = np.array(trades)
    return {
        "return": float(eq_s.iloc[-1] - 1),
        "buy_hold_return": float(bh[-1] - 1),
        "mdd": float(dd.min()),
        "trades": len(trades),
        "win_rate": float((t > 0).mean()) if len(t) else float("nan"),
        "avg_trade": float(t.mean()) if len(t) else float("nan"),
        "in_position": bool(pos),
        "pending": pending,
    }


# ---------- 오늘 상태 ----------
def evaluate(px: pd.DataFrame, flows: pd.DataFrame | None, strat: dict, cost: float) -> dict:
    buy, sell, bparts, sparts = strategy_signals(px, flows, strat)
    sim = simulate(px, buy, sell, strat.get("stop_loss"), strat.get("take_profit"), cost)
    state = "해당없음"
    if bool(buy.iloc[-1]):
        state = "매수 조건 충족"
    elif bool(sell.iloc[-1]):
        state = "매도 조건 충족"
    return {
        "state": state,
        "buy_detail": {label(c): bool(s.iloc[-1]) for c, s in zip(strat["buy_all"], bparts)},
        "sell_detail": {label(c): bool(s.iloc[-1]) for c, s in zip(strat.get("sell_any", []), sparts)},
        "backtest": sim,
    }
