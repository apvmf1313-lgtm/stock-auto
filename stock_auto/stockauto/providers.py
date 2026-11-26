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


class KisProvider(LiveProvider):
    """가격·배당·환율은 yfinance, 수급·PER/PBR은 한국투자증권 Open API(시세 조회만 사용, 주문 기능 없음).

    환경변수: KIS_APP_KEY, KIS_APP_SECRET. env="demo"면 모의투자 서버(모의 키), "real"이면 실전 서버.
    투자자 매매동향 API는 최근 약 30거래일만 주므로, 받은 값을 cache_dir 에 CSV로 쌓아 기간을 늘린다.
    """

    BASES = {"real": "https://openapi.koreainvestment.com:9443",
             "demo": "https://openapivts.koreainvestment.com:29443"}

    def __init__(self, app_key: str, app_secret: str, env: str = "demo", cache_dir=None, session=None):
        import requests

        self.key, self.secret = app_key, app_secret
        self.base = self.BASES[env]
        self.pause = 0.6 if env == "demo" else 0.08   # 초당 호출 제한 대응
        self.cache_dir = cache_dir
        self.http = session or requests.Session()
        self._token = None
        self._token_error = None

    def _token_get(self) -> str:
        if self._token_error:              # 토큰 발급은 분당 1회 제한 → 실패 시 재시도하지 않음
            raise self._token_error
        if self._token is None:
            r = self.http.post(f"{self.base}/oauth2/tokenP", timeout=15, json={
                "grant_type": "client_credentials", "appkey": self.key, "appsecret": self.secret})
            data = r.json()
            if "access_token" not in data:
                self._token_error = RuntimeError(f"KIS 토큰 발급 실패: {data.get('error_description') or data}")
                raise self._token_error
            self._token = data["access_token"]
        return self._token

    def _get(self, path: str, tr_id: str, params: dict) -> dict:
        import time

        time.sleep(self.pause)
        r = self.http.get(f"{self.base}{path}", params=params, timeout=15, headers={
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._token_get()}",
            "appkey": self.key, "appsecret": self.secret, "tr_id": tr_id, "custtype": "P"})
        data = r.json()
        if data.get("rt_cd") != "0":
            raise RuntimeError(f"KIS {tr_id} 오류: {data.get('msg1') or data}")
        return data

    def fundamentals(self, codes, date):
        rows = {}
        for c in codes:
            try:
                o = self._get("/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
                              {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": c})["output"]
            except Exception:
                if self._token_error:
                    raise
                o = {}               # 한 종목 실패는 NaN 처리
            rows[c] = {"per": pd.to_numeric(o.get("per"), errors="coerce"),
                       "pbr": pd.to_numeric(o.get("pbr"), errors="coerce")}
        return pd.DataFrame.from_dict(rows, orient="index").reindex(codes)

    @staticmethod
    def parse_investor(output: list) -> pd.DataFrame:
        """순매수 '수량' × 종가 = 순매수 금액(원). 거래대금 필드의 단위 문제를 피하기 위해 수량 기준으로 계산."""
        recs = []
        for o in output:
            q = {k: pd.to_numeric(o.get(f"{k}_ntby_qty"), errors="coerce") for k in ("frgn", "orgn", "prsn")}
            close = pd.to_numeric(o.get("stck_clpr"), errors="coerce")
            if not o.get("stck_bsop_date") or pd.isna(q["frgn"]) or pd.isna(close):
                continue   # 장중 미확정 행 등
            recs.append({"date": pd.Timestamp(o["stck_bsop_date"]), "foreign": q["frgn"] * close,
                         "institution": q["orgn"] * close, "individual": q["prsn"] * close})
        if not recs:
            raise ValueError("투자자 매매동향 응답에 유효한 행이 없음")
        return pd.DataFrame(recs).set_index("date").sort_index()

    def flows(self, code, start, end):
        out = self._get("/uapi/domestic-stock/v1/quotations/inquire-investor", "FHKST01010900",
                        {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code})["output"]
        fresh = self.parse_investor(out)
        df = merge_cache(fresh, self.cache_dir, code)
        return df.loc[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))]


def merge_cache(fresh: pd.DataFrame, cache_dir, code: str) -> pd.DataFrame:
    """새로 받은 수급을 CSV 캐시와 합쳐 저장하고 전체 이력을 돌려준다."""
    if cache_dir is None:
        return fresh
    import pathlib

    p = pathlib.Path(cache_dir) / f"{code}.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        old = pd.read_csv(p, index_col=0, parse_dates=True)
        fresh = pd.concat([old, fresh])
        fresh = fresh[~fresh.index.duplicated(keep="last")].sort_index()
    fresh.to_csv(p)
    return fresh


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
