import pandas as pd
import pytest

from stockauto.providers import KisProvider


class Resp:
    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


class FakeSession:
    def __init__(self, token_ok=True):
        self.token_ok, self.posts, self.gets = token_ok, 0, []

    def post(self, url, json, timeout):
        self.posts += 1
        return Resp({"access_token": "T"} if self.token_ok else {"error_description": "키 오류"})

    def get(self, url, params, timeout, headers):
        self.gets.append((url, headers["tr_id"], headers["authorization"]))
        if headers["tr_id"] == "FHKST01010100":
            return Resp({"rt_cd": "0", "output": {"per": "12.50", "pbr": "1.20"}})
        return Resp({"rt_cd": "0", "output": [
            {"stck_bsop_date": "20261126", "stck_clpr": "", "frgn_ntby_qty": "", "orgn_ntby_qty": "", "prsn_ntby_qty": ""},
            {"stck_bsop_date": "20261125", "stck_clpr": "1000", "frgn_ntby_qty": "10", "orgn_ntby_qty": "-4", "prsn_ntby_qty": "-6"},
            {"stck_bsop_date": "20261124", "stck_clpr": "900", "frgn_ntby_qty": "-2", "orgn_ntby_qty": "1", "prsn_ntby_qty": "1"},
        ]})


def kis(sess, tmp_path=None):
    k = KisProvider("k", "s", env="real", cache_dir=tmp_path, session=sess)
    k.pause = 0
    return k


def test_fundamentals_parse():
    s = FakeSession()
    f = kis(s).fundamentals(["005930", "000660"], "2026-11-26")
    assert f.loc["005930", "per"] == pytest.approx(12.5) and f.loc["000660", "pbr"] == pytest.approx(1.2)
    assert s.posts == 1 and s.gets[0][2] == "Bearer T"     # 토큰은 한 번만 발급


def test_flows_value_and_skip_unconfirmed_row(tmp_path):
    f = kis(FakeSession(), tmp_path).flows("005930", "2026-01-01", "2026-12-31")
    assert list(f.index.strftime("%Y%m%d")) == ["20261124", "20261125"]   # 미확정 행 제외, 날짜 오름차순
    assert f.loc["2026-11-25", "foreign"] == 10_000 and f.loc["2026-11-25", "institution"] == -4_000
    assert (tmp_path / "005930.csv").exists()


def test_cache_accumulates(tmp_path):
    old = pd.DataFrame({"foreign": [1.0], "institution": [2.0], "individual": [-3.0]},
                       index=pd.to_datetime(["2026-10-01"]))
    old.to_csv(tmp_path / "005930.csv")
    f = kis(FakeSession(), tmp_path).flows("005930", "2026-01-01", "2026-12-31")
    assert len(f) == 3 and f.index[0] == pd.Timestamp("2026-10-01")


def test_token_failure_not_retried():
    s = FakeSession(token_ok=False)
    k = kis(s)
    with pytest.raises(RuntimeError):
        k.fundamentals(["005930"], "x")
    with pytest.raises(RuntimeError):
        k.flows("005930", "2026-01-01", "2026-12-31")
    assert s.posts == 1


def test_rate_limit_retry():
    class Limited(FakeSession):
        calls = 0

        def get(self, url, params, timeout, headers):
            Limited.calls += 1
            if Limited.calls == 1:
                return Resp({"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."})
            return super().get(url, params, timeout, headers)

    f = kis(Limited()).fundamentals(["005930"], "x")
    assert f.loc["005930", "per"] == pytest.approx(12.5) and Limited.calls == 2
