"""데이터 공급자. 외부 사이트 스크래핑 대신 공식/공개 라이브러리(yfinance, pykrx)를 사용한다.

표준 형태
- prices(): index=날짜, columns=[close, adj_close, volume, dividend]
- fundamentals(): index=코드, columns=[per, pbr]
- flows(): index=날짜, columns=[foreign, institution, individual] (순매수 금액, 원)
- fx(): index=날짜, Series USDKRW
"""
from __future__ import annotations

import numpy as np
import pandas as pd


class Provider:
    def prices(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        raise NotImplementedError

    def fundamentals(self, codes: list[str], date: str) -> pd.DataFrame:
        raise NotImplementedError

    def flows(self, code: str, start: str, end: str) -> pd.DataFrame:
        raise NotImplementedError

    def fx(self, start: str, end: str) -> pd.Series:
        raise NotImplementedError


class LiveProvider(Provider):
    """yfinance(가격·배당·환율) + pykrx(수급·PER/PBR). 설치/네트워크가 필요하다."""

    def prices(self, symbol, start, end):
        import yfinance as yf

        h = yf.Ticker(symbol).history(start=start, end=end, auto_adjust=False)
        if h.empty:
            raise ValueError(f"가격 데이터 없음: {symbol}")
        h.index = pd.to_datetime(h.index).tz_localize(None).normalize()
        out = pd.DataFrame(
            {
                "close": h["Close"],
                "adj_close": h["Adj Close"] if "Adj Close" in h else h["Close"],
                "volume": h["Volume"],
                "dividend": h.get("Dividends", 0.0),
            }
        )
        return out.dropna(subset=["close"])

    def fundamentals(self, codes, date):
        from pykrx import stock

        d = date.replace("-", "")
        frames = []
        for market in ("KOSPI", "KOSDAQ"):
            f = stock.get_market_fundamental_by_ticker(d, market=market)
            frames.append(f)
        f = pd.concat(frames)
        f = f.rename(columns={"PER": "per", "PBR": "pbr"})[["per", "pbr"]]
        return f.reindex(codes)

    def flows(self, code, start, end):
        from pykrx import stock

        f = stock.get_market_trading_value_by_date(
            start.replace("-", ""), end.replace("-", ""), code
        )
        f = f.rename(
            columns={"외국인합계": "foreign", "기관합계": "institution", "개인": "individual"}
        )
        f.index = pd.to_datetime(f.index)
        return f[["foreign", "institution", "individual"]]

    def fx(self, start, end):
        import yfinance as yf

        h = yf.Ticker("KRW=X").history(start=start, end=end)
        s = h["Close"]
        s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
        return s.rename("usdkrw")


class SyntheticProvider(Provider):
    """오프라인 테스트/데모용 가상 데이터. 심볼별로 재현 가능(seed 고정). 실제 시세가 아니다."""

    def _rng(self, key: str):
        return np.random.default_rng(abs(hash_str(key)) % (2**32))

    def _dates(self, start, end):
        return pd.bdate_range(start, end)

    def prices(self, symbol, start, end):
        r = self._rng("p" + symbol)
        idx = self._dates(start, end)
        n = len(idx)
        ret = r.normal(0.0003, 0.015, n)
        close = 50000 * np.exp(np.cumsum(ret))
        div = np.zeros(n)
        # 분기 배당(대략 3개월마다) 약 0.6%
        for i in range(60, n, 63):
            div[i] = close[i] * 0.006
        # adj_close: 배당 재투자 가정(미래 배당만큼 과거를 할인)
        factor = np.ones(n)
        for i in np.where(div > 0)[0]:
            factor[:i] *= 1 - div[i] / close[i - 1]
        vol = r.integers(5e5, 3e6, n).astype(float)
        return pd.DataFrame(
            {"close": close, "adj_close": close * factor, "volume": vol, "dividend": div},
            index=idx,
        )

    def fundamentals(self, codes, date):
        r = self._rng("f" + date)
        return pd.DataFrame(
            {"per": r.uniform(5, 40, len(codes)), "pbr": r.uniform(0.4, 5, len(codes))},
            index=codes,
        )

    def flows(self, code, start, end):
        r = self._rng("w" + code)
        idx = self._dates(start, end)
        n = len(idx)
        f = r.normal(0, 3e9, n)
        i = r.normal(0, 2e9, n)
        # 마지막 30일은 외국인·기관 매집 패턴을 일부 종목에 주입
        if hash_str(code) % 2 == 0:
            f[-30:] += 4e9
            i[-30:] += 2e9
        return pd.DataFrame(
            {"foreign": f, "institution": i, "individual": -(f + i)}, index=idx
        )

    def fx(self, start, end):
        r = self._rng("fx")
        idx = self._dates(start, end)
        return pd.Series(1350 * np.exp(np.cumsum(r.normal(0, 0.003, len(idx)))), index=idx, name="usdkrw")


def hash_str(s: str) -> int:
    """파이썬 hash()는 실행마다 달라지므로 결정적 해시를 쓴다."""
    h = 0
    for ch in s:
        h = (h * 131 + ord(ch)) % (2**61 - 1)
    return h
