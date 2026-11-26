import numpy as np
import pandas as pd
import pytest

from stockauto import analytics as an
from stockauto import taxes
from stockauto.providers import SyntheticProvider


def mk_px(close, adj=None, div=None, vol=None):
    idx = pd.bdate_range("2023-01-02", periods=len(close))
    return pd.DataFrame({
        "close": close, "adj_close": adj if adj is not None else close,
        "volume": vol if vol is not None else [1000.0] * len(close),
        "dividend": div if div is not None else [0.0] * len(close)}, index=idx)


def test_total_return_includes_distribution():
    px = mk_px([100.0, 90.0, 95.0], adj=[100.0, 92.0, 99.0])
    r = an.total_return(px, 2)
    assert r["price_return"] == pytest.approx(-0.05)
    assert r["total_return"] == pytest.approx(-0.01)
    assert r["distribution_contrib"] == pytest.approx(0.04)


def test_dividend_yield_ttm():
    div = [0.0] * 300
    div[100] = 2.0
    div[200] = 2.0
    px = mk_px([100.0] * 300, div=div)
    assert an.dividend_yield(px, 365) == pytest.approx(0.04)


def test_backtest_lump_sum_flat_price():
    px = mk_px([100.0] * 120)
    r = an.backtest(px, "2023-01-02", 1_000_000, 0)
    assert r["final_total"] == pytest.approx(1_000_000)
    assert r["mdd"] == pytest.approx(0)


def test_backtest_monthly_adds_contributions():
    px = mk_px([100.0] * 130)  # ~6개월
    r = an.backtest(px, "2023-01-02", 1_000_000, 100_000)
    months_added = r["invested"] / 100_000 - 10
    assert months_added >= 4
    assert r["final_total"] == pytest.approx(r["invested"])


def test_valuation_percentile_and_min_peers():
    fund = pd.DataFrame({"per": [5, 10, 15, 20, -3], "pbr": [0.5, 1, 1.5, 2, 3]},
                        index=list("abcde"))
    out = an.valuation_percentile(fund, {c: "G" for c in "abcde"}, min_peers=4)
    assert out.loc["a", "per_pct"] < out.loc["d", "per_pct"]
    assert np.isnan(out.loc["e", "per_pct"])        # 적자는 순위 제외
    small = an.valuation_percentile(fund.iloc[:3], {c: "G" for c in "abc"}, min_peers=4)
    assert small["per_pct"].isna().all()            # 표본 부족


def test_flow_state_thresholds():
    s = {20: {"smart_ratio": 0.05}}
    assert an.flow_state(s, 0.02, -0.02) == "매집"
    assert an.flow_state({20: {"smart_ratio": -0.05}}, 0.02, -0.02) == "이탈"
    assert an.flow_state({20: {"smart_ratio": 0.0}}, 0.02, -0.02) == "중립"


def test_gap_scan_zero_premium_and_shock():
    idx = pd.bdate_range("2023-01-02", periods=100)
    us = pd.Series(10.0, index=idx)
    fx = pd.Series(1000.0, index=idx)
    kr = pd.Series(10000.0 + np.random.default_rng(0).normal(0, 20, 100), index=idx)
    kr.iloc[-1] = 10500.0
    g = an.gap_scan(kr, us, fx)
    assert g["premium"] == pytest.approx(0.05)
    assert g["z"] > 3


def test_us_tax():
    assert taxes.us_capital_gains_tax(10_000_000, 0.22, 2_500_000) == pytest.approx(1_650_000)
    assert taxes.us_capital_gains_tax(1_000_000, 0.22, 2_500_000) == 0


def test_account_compare():
    cfg = dict(general_div_rate=0.154, isa_exempt_krw=2_000_000, isa_excess_rate=0.099, irp_pension_rate=0.055)
    r = taxes.compare_accounts(3_000_000, cfg)
    assert r["general"] == pytest.approx(462_000)
    assert r["isa"] == pytest.approx(99_000)
    assert r["irp_now"] == 0


def test_end_to_end_synthetic(tmp_path):
    import datetime as dt
    from stockauto import pipeline, report
    cfg = pipeline.load_config("config.yaml")
    res = pipeline.run(cfg, SyntheticProvider(), today=dt.date(2026, 11, 24))
    assert res["checklist"] and not res["errors"]
    html = report.render(res, synthetic=True)
    assert "합성 데이터" in html and "권유" in html
