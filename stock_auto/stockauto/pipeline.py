"""전체 흐름: 수집 → 계산 → 관찰규칙 평가 → 결과 dict. (리포트 렌더링은 report.py)"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import yaml

from . import analytics as an
from . import paper
from . import signals as sg
from . import taxes
from .providers import Provider


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def run(cfg: dict, provider: Provider, today: dt.date | None = None, ledger: dict | None = None) -> dict:
    today = today or dt.date.today()
    end = today.isoformat()
    start = (today - dt.timedelta(days=cfg["report"]["lookback_days"])).isoformat()
    bt_start = min(cfg["backtest"]["start"], start)
    wl = {w["name"]: w for w in cfg["watchlist"]}
    errors: list[str] = []

    # 1) 가격 수집
    prices: dict[str, pd.DataFrame] = {}
    for name, w in wl.items():
        try:
            prices[name] = provider.prices(w["yahoo"], bt_start, end)
        except Exception as e:  # 한 종목 실패가 전체를 막지 않게
            errors.append(f"가격 수집 실패 {name}: {e}")

    # 2) 센트빈: 총수익률
    income = []
    for name in cfg.get("income_etfs", []):
        if name not in prices:
            continue
        px = prices[name]
        row = {"name": name, "div_yield_ttm": an.dividend_yield(px)}
        for d in (90, 180, 365):
            row[f"tr_{d}"] = an.total_return(px, d)
        income.append(row)

    # 3) 스노우볼: 백테스트
    bt_cfg = cfg["backtest"]
    backtests = []
    for name in bt_cfg["tickers"]:
        if name in prices:
            try:
                r = an.backtest(prices[name], bt_cfg["start"], bt_cfg["initial_krw"], bt_cfg["monthly_krw"])
                backtests.append({"name": name, **r})
            except Exception as e:
                errors.append(f"백테스트 실패 {name}: {e}")

    # 4) 핀트릭스: 그룹 내 밸류에이션 (KRX 코드 있는 종목만)
    kr = {w["name"]: w for w in cfg["watchlist"] if w.get("code")}
    code2name = {w["code"]: n for n, w in kr.items()}
    val = pd.DataFrame()
    try:
        fund = provider.fundamentals(list(code2name), end)
        val = an.valuation_percentile(fund, {w["code"]: w["group"] for w in kr.values()})
        val.index = [code2name[c] for c in val.index]
    except Exception as e:
        errors.append(f"PER/PBR 수집 실패: {e}")

    # 5) 세콤달/WhoBuy: 수급
    flow_cfg = cfg["flows"]
    flows_out = {}
    flow_raw: dict[str, pd.DataFrame] = {}
    for name, w in kr.items():
        if name not in prices:
            continue
        try:
            f = provider.flows(w["code"], bt_start, end)
            flow_raw[name] = f
            s = an.flow_summary(f, prices[name], tuple(flow_cfg["windows"]))
            flows_out[name] = {
                "summary": s,
                "state": an.flow_state(s, flow_cfg["accumulate_threshold"], flow_cfg["exit_threshold"]),
            }
        except Exception as e:
            errors.append(f"수급 수집 실패 {name}: {e}")

    # 6) 데일리아카이브: 한·미 괴리율
    gaps = []
    try:
        fx = provider.fx(start, end)
        for pair in cfg.get("gap_pairs", []):
            try:
                kr_px = provider.prices(pair["kr"], start, end)["close"]
                us_px = provider.prices(pair["us"], start, end)["close"]
                g = an.gap_scan(kr_px, us_px, fx, pair.get("ratio", 1.0))
                g["name"] = pair["name"]
                g["alert"] = abs(g["z"]) >= cfg["gap_z_threshold"] if g["z"] == g["z"] else False
                gaps.append(g)
            except Exception as e:
                errors.append(f"괴리율 실패 {pair['name']}: {e}")
    except Exception as e:
        errors.append(f"환율 수집 실패: {e}")

    # 6.5) 사용자 정의 매수·매도 조건 알림 + 과거 백테스트
    sig_out = []
    for strat in cfg.get("signals", []):
        for name in strat.get("tickers", []):
            if name not in prices:
                continue
            try:
                r = sg.evaluate(prices[name], flow_raw.get(name), strat, cfg.get("signal_cost_per_side", 0.0015))
                sig_out.append({"strategy": strat["name"], "name": name, **r})
            except Exception as e:
                errors.append(f"시그널 실패 {strat['name']}/{name}: {e}")

    # 6.6) 모의 매매(가상 계좌) — 장부가 주어졌을 때만
    paper_out = None
    if ledger is not None and cfg.get("paper", {}).get("enabled", True):
        try:
            paper.update(ledger, cfg, prices, flow_raw, end)
            paper_out = paper.summarize(ledger)
        except Exception as e:
            errors.append(f"모의 매매 실패: {e}")

    # 7) 관찰 규칙 (매수/매도 신호 아님 — 조건 충족 체크리스트)
    rules = cfg["rules"]
    checklist = []
    for name, w in wl.items():
        if name not in prices:
            continue
        checks = {}
        if name in val.index and pd.notna(val.loc[name, "per_pct"]):
            checks["밸류에이션 하위"] = bool(
                val.loc[name, "per_pct"] <= rules["cheap_percentile"]
                and val.loc[name, "pbr_pct"] <= rules["cheap_percentile"]
            )
        else:
            checks["밸류에이션 하위"] = None  # 데이터 없음/표본 부족
        tr = an.total_return(prices[name], rules["momentum_days"])
        checks[f"{rules['momentum_days']}일 총수익률>0"] = tr["total_return"] > 0
        if rules.get("require_flow_accumulate"):
            checks["수급 매집"] = (flows_out[name]["state"] == "매집") if name in flows_out else None
        known = [v for v in checks.values() if v is not None]
        checklist.append({
            "name": name,
            "checks": checks,
            "met": sum(known),
            "evaluable": len(known),
            "last_close": float(prices[name]["close"].iloc[-1]),
            "spark": prices[name]["close"].iloc[-120:].tolist(),
        })
    checklist.sort(key=lambda r: (r["met"] / r["evaluable"]) if r["evaluable"] else -1, reverse=True)

    # 8) 세금 비교: 월 적립 백테스트 종목의 연 배당 추정 기준
    tax_cmp = []
    for name in cfg.get("income_etfs", []):
        if name in prices:
            # 연 적립액 기준 TTM 분배율로 연 분배금을 근사 (단순화)
            annual_base = bt_cfg["initial_krw"] + bt_cfg["monthly_krw"] * 12
            est_div = annual_base * an.dividend_yield(prices[name])
            tax_cmp.append({"name": name, "est_dividend": est_div,
                            **taxes.compare_accounts(est_div, cfg["tax"])})

    return {
        "as_of": end,
        "income": income,
        "backtests": backtests,
        "valuation": val,
        "flows": flows_out,
        "gaps": gaps,
        "signals": sig_out,
        "paper": paper_out,
        "checklist": checklist,
        "tax": tax_cmp,
        "errors": errors,
    }
