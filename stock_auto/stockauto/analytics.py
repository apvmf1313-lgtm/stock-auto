"""핵심 계산 모듈. 사이트별 기능과의 대응:

- total_return / dividend_yield : 센트빈 (분배율이 아닌 NAV 포함 총수익률)
- backtest                      : 스노우볼 (배당 재투자 기준 과거 백테스트)
- valuation_percentile          : 핀트릭스 (동종 그룹 내 PER·PBR 위치)
- flow_summary                  : 세콤달.콤 / Who Buy? (기간별 수급 누적·쏠림)
- gap_scan                      : 데일리아카이브 (한·미 괴리율 z-score)
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ---------- 센트빈: 총수익률 ----------
def total_return(px: pd.DataFrame, days: int) -> dict:
    p = px.iloc[-(days + 1):] if len(px) > days else px
    price_ret = p["close"].iloc[-1] / p["close"].iloc[0] - 1
    total_ret = p["adj_close"].iloc[-1] / p["adj_close"].iloc[0] - 1
    return {
        "price_return": float(price_ret),      # NAV(가격) 변동만
        "total_return": float(total_ret),      # 분배금 재투자 포함
        "distribution_contrib": float(total_ret - price_ret),
    }


def dividend_yield(px: pd.DataFrame, days: int = 365) -> float:
    """최근 days 달력일 분배금 합 / 현재가 (trailing)."""
    cutoff = px.index[-1] - pd.Timedelta(days=days)
    div = px.loc[px.index > cutoff, "dividend"].sum()
    return float(div / px["close"].iloc[-1])


# ---------- 스노우볼: 백테스트 ----------
def backtest(px: pd.DataFrame, start: str, initial: float, monthly: float = 0.0) -> dict:
    """일시 투자 + 월 적립. adj_close 기준이면 배당 재투자, close 기준이면 가격만."""
    p = px.loc[px.index >= pd.Timestamp(start)]
    if len(p) < 30:
        raise ValueError("백테스트 기간 데이터 부족")

    def run(col: str):
        price = p[col]
        shares = initial / price.iloc[0]
        invested = initial
        last_month = p.index[0].to_period("M")
        values = []
        for dt, pr in price.items():
            m = dt.to_period("M")
            if m != last_month:           # 월 첫 거래일에 적립
                shares += monthly / pr
                invested += monthly
                last_month = m
            values.append(shares * pr)
        v = pd.Series(values, index=p.index)
        return v, invested

    v_tr, invested = run("adj_close")
    v_pr, _ = run("close")
    dd = v_tr / v_tr.cummax() - 1
    daily = v_tr.pct_change().dropna()
    return {
        "invested": float(invested),
        "final_total": float(v_tr.iloc[-1]),
        "final_price_only": float(v_pr.iloc[-1]),
        "multiple_total": float(v_tr.iloc[-1] / invested),
        "mdd": float(dd.min()),
        "vol_annual": float(daily.std() * np.sqrt(252)),
        "series": v_tr,
    }


# ---------- 핀트릭스: 그룹 내 밸류에이션 위치 ----------
def valuation_percentile(fund: pd.DataFrame, groups: dict[str, str], min_peers: int = 4) -> pd.DataFrame:
    """fund: index=코드, per/pbr. groups: 코드->그룹. 반환 pct는 낮을수록 그룹 내 저평가 쪽.

    PER<=0(적자)은 순위에서 제외. 그룹 표본이 min_peers 미만이면 NaN(신뢰 불가).
    """
    df = fund.copy()
    df["group"] = [groups.get(c) for c in df.index]
    df.loc[df["per"] <= 0, "per"] = np.nan
    df.loc[df["pbr"] <= 0, "pbr"] = np.nan
    out = []
    for g, sub in df.groupby("group"):
        sub = sub.copy()
        ok = len(sub) >= min_peers
        sub["per_pct"] = sub["per"].rank(pct=True) if ok else np.nan
        sub["pbr_pct"] = sub["pbr"].rank(pct=True) if ok else np.nan
        sub["peers"] = len(sub)
        out.append(sub)
    return pd.concat(out) if out else df


# ---------- 세콤달 / Who Buy: 수급 ----------
def flow_summary(flows: pd.DataFrame, px: pd.DataFrame, windows=(5, 20, 60)) -> dict:
    traded = (px["close"] * px["volume"]).reindex(flows.index).ffill()
    res = {}
    for w in windows:
        f = flows.iloc[-w:]
        t = traded.iloc[-w:].sum()
        res[w] = {
            "foreign": float(f["foreign"].sum()),
            "institution": float(f["institution"].sum()),
            "individual": float(f["individual"].sum()),
            # 거래대금 대비 외국인+기관 순매수 비율 (쏠림 강도)
            "smart_ratio": float((f["foreign"].sum() + f["institution"].sum()) / t) if t else np.nan,
        }
    return res


def flow_state(summary: dict, accumulate: float, exit_: float, window: int = 20) -> str:
    r = summary[window]["smart_ratio"]
    if np.isnan(r):
        return "판단불가"
    if r >= accumulate:
        return "매집"
    if r <= exit_:
        return "이탈"
    return "중립"


# ---------- 데일리아카이브: 한·미 괴리율 ----------
def gap_scan(kr: pd.Series, us: pd.Series, fx: pd.Series, ratio: float = 1.0, z_window: int = 60) -> dict:
    df = pd.concat({"kr": kr, "us": us, "fx": fx}, axis=1).ffill().dropna()
    prem = df["kr"] / (df["us"] * df["fx"] * ratio) - 1
    hist = prem.iloc[-z_window:]
    sd = hist.std()
    z = (prem.iloc[-1] - hist.mean()) / sd if sd > 0 else np.nan
    return {"premium": float(prem.iloc[-1]), "z": float(z), "mean": float(hist.mean())}
