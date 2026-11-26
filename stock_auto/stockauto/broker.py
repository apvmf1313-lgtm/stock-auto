"""한국투자증권 **모의투자** 계좌 주문·잔고 모듈.

안전장치
- 모의투자 서버(openapivts)에만 연결된다. 실전 서버 주소나 실전 설정이면 생성 단계에서 거부한다.
- 주문 거래 ID는 모의 전용(V로 시작)만 쓴다.
- 1회 주문 금액이 규칙 배정액의 105%를 넘으면 주문하지 않는다.

흐름
- 저녁(18:30) run_daily.py: 가상 장부(paper)가 다음 날 주문을 '예약(pending)'한다.
- 아침(09:05) broker_orders.py: 선택한 규칙의 예약을 읽어 모의 계좌에 시장가 주문을 넣는다(매도 먼저, 매수 나중).
- 같은 예약은 한 번만 주문한다(broker_ledger 의 done 기록).
"""
from __future__ import annotations

import json
import math
import pathlib

from .providers import KisProvider

DEMO_BASE = KisProvider.BASES["demo"]
TR = {"buy": "VTTC0012U", "sell": "VTTC0011U", "balance": "VTTC8434R"}
TR_OLD = {"buy": "VTTC0802U", "sell": "VTTC0801U"}   # 구 규격(신 규격 거부 시 한 번 재시도)


def parse_account(acct: str) -> tuple[str, str]:
    digits = "".join(ch for ch in acct if ch.isdigit())
    if len(digits) == 8:
        return digits, "01"
    if len(digits) == 10:
        return digits[:8], digits[8:]
    raise ValueError("계좌번호는 8자리(또는 8자리+상품코드 2자리)여야 합니다")


class MockBroker:
    def __init__(self, kis: KisProvider, account: str):
        if kis.base != DEMO_BASE:
            raise RuntimeError("안전장치: 모의투자 서버가 아니면 주문 기능을 사용할 수 없습니다 (config.yaml kis.env 를 demo 로)")
        self.kis = kis
        self.cano, self.prdt = parse_account(account)

    def _post(self, path: str, tr_id: str, body: dict) -> dict:
        import time

        assert tr_id.startswith("V"), "안전장치: 모의 거래 ID만 허용"
        time.sleep(self.kis.pause)
        r = self.kis.http.post(f"{self.kis.base}{path}", json=body, timeout=15, headers={
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.kis._token_get()}",
            "appkey": self.kis.key, "appsecret": self.kis.secret, "tr_id": tr_id, "custtype": "P"})
        return r.json()

    def price(self, code: str) -> float:
        o = self.kis._get("/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
                          {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code})["output"]
        return float(o["stck_prpr"])

    def balance(self) -> dict:
        d = self.kis._get("/uapi/domestic-stock/v1/trading/inquire-balance", TR["balance"], {
            "CANO": self.cano, "ACNT_PRDT_CD": self.prdt, "AFHR_FLPR_YN": "N", "OFL_YN": "",
            "INQR_DVSN": "02", "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00", "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""})
        num = lambda v: float(v) if v not in (None, "") else 0.0
        holdings = {}
        for o in d.get("output1", []):
            q = int(num(o.get("hldg_qty")))
            if q > 0:
                holdings[o["pdno"]] = {
                    "name": o.get("prdt_name", "").strip(), "qty": q, "sellable": int(num(o.get("ord_psbl_qty"))),
                    "avg": num(o.get("pchs_avg_pric")), "price": num(o.get("prpr")),
                    "value": num(o.get("evlu_amt")), "pnl": num(o.get("evlu_pfls_amt")),
                    "pnl_rt": num(o.get("evlu_pfls_rt")) / 100}
        s = (d.get("output2") or [{}])[0]
        return {"cash": num(s.get("dnca_tot_amt")), "total": num(s.get("tot_evlu_amt")),
                "pnl": num(s.get("evlu_pfls_smtl_amt")), "holdings": holdings}

    def order(self, code: str, qty: int, side: str) -> dict:
        body = {"CANO": self.cano, "ACNT_PRDT_CD": self.prdt, "PDNO": code,
                "ORD_DVSN": "01", "ORD_QTY": str(int(qty)), "ORD_UNPR": "0", "EXCG_ID_DVSN_CD": "KRX"}
        d = self._post("/uapi/domestic-stock/v1/trading/order-cash", TR[side], body)
        if d.get("rt_cd") != "0":
            first = d
            # 신 규격이 모의 서버에서 거부되는 경우 대비: 구 규격으로 한 번만 재시도 (첫 주문이 거부됐으므로 중복 주문 아님)
            body.pop("EXCG_ID_DVSN_CD")
            d = self._post("/uapi/domestic-stock/v1/trading/order-cash", TR_OLD[side], body)
            if d.get("rt_cd") != "0":
                d = first
        ok = d.get("rt_cd") == "0"
        return {"ok": ok, "odno": (d.get("output") or {}).get("ODNO"), "msg": str(d.get("msg1", "")).strip()}


# ---------- 장부 ----------
def load(path) -> dict:
    p = pathlib.Path(path)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"version": 1, "slots": {}, "done": {}, "orders": []}


def save(led: dict, path) -> None:
    led["orders"] = led["orders"][-300:]
    pathlib.Path(path).write_text(json.dumps(led, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------- 주문 계획 (순수 함수: 테스트 가능) ----------
def plan(cfg: dict, paper_led: dict, bled: dict) -> list[dict]:
    """가상 장부의 예약 중 계좌에 적용할 규칙의 것만 골라 주문 후보를 만든다(수량은 실행 시 결정)."""
    bcfg = cfg["broker"]
    chosen = bcfg["strategies"]
    strat_map = {s["name"]: s for s in cfg.get("signals", [])}
    codes = {w["name"]: w.get("code") for w in cfg["watchlist"]}
    budget_per_strat = bcfg["capital_krw"] / max(1, len(chosen))
    out = []
    for sname in chosen:
        st = strat_map.get(sname)
        if not st:
            continue
        tickers = [t for t in st["tickers"] if codes.get(t)]
        slot_budget = budget_per_strat / max(1, len(tickers))
        for t in tickers:
            key = f"{sname}|{t}"
            ps = paper_led.get("slots", {}).get(key)
            if not ps or ps.get("pending") not in ("buy", "sell"):
                continue
            token = f"{ps['last_bar']}|{ps['pending']}"
            if bled["done"].get(key) == token:
                continue
            out.append({"key": key, "strategy": sname, "name": t, "code": codes[t], "side": ps["pending"],
                        "reason": ps.get("pending_reason"), "token": token, "budget": slot_budget})
    out.sort(key=lambda a: 0 if a["side"] == "sell" else 1)   # 매도 먼저
    return out


def execute(actions: list[dict], broker, bled: dict, today: str) -> list[str]:
    msgs = []
    if not actions:
        return msgs
    bal = broker.balance()
    cash = bal["cash"]
    for a in actions:
        slot = bled["slots"].setdefault(a["key"], {"qty": 0})
        rec = {"date": today, **{k: a[k] for k in ("key", "name", "code", "side", "reason")}}
        if a["side"] == "sell":
            sellable = bal["holdings"].get(a["code"], {}).get("sellable", 0)
            qty = min(slot["qty"], sellable)
            if qty <= 0:
                bled["done"][a["key"]] = a["token"]
                slot["qty"] = 0
                msgs.append(f"{a['name']}({a['strategy']}) 매도 건너뜀: 보유 수량 없음")
                continue
        else:
            if slot["qty"] > 0:
                bled["done"][a["key"]] = a["token"]
                continue
            px = broker.price(a["code"])
            qty = math.floor(min(a["budget"], cash / 1.003) / px)
            if qty <= 0:
                bled["done"][a["key"]] = a["token"]
                why = "현금 부족" if cash < px else f"배정액 {a['budget']:,.0f}원 < 1주 {px:,.0f}원"
                msgs.append(f"{a['name']}({a['strategy']}) 매수 건너뜀: {why}")
                rec.update(ok=False, qty=0, msg=why)
                bled["orders"].append(rec)
                continue
            if qty * px > a["budget"] * 1.05:
                raise RuntimeError("안전장치: 배정액 초과 주문")
            rec["est_price"] = px
        res = broker.order(a["code"], qty, a["side"])
        rec.update(qty=qty, **res)
        bled["orders"].append(rec)
        side_kr = "매수" if a["side"] == "buy" else "매도"
        if res["ok"]:
            bled["done"][a["key"]] = a["token"]
            if a["side"] == "buy":
                slot.update(qty=qty, entry_date=today)
                cash -= qty * rec["est_price"] * 1.003
            else:
                slot.update(qty=slot["qty"] - qty)
            msgs.append(f"✅ {a['name']} {side_kr} {qty}주 시장가 ({a['strategy']}, {a['reason']})")
        else:
            msgs.append(f"❌ {a['name']} {side_kr} {qty}주 실패: {res['msg']}")
    return msgs
