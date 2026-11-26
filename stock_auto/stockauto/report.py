"""결과 dict → 단일 HTML 리포트 (+ JSON 요약)."""
from __future__ import annotations

import html
import json

import pandas as pd

CSS = """
body{font-family:-apple-system,'Malgun Gothic',sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;color:#1c1c1e}
h1{font-size:22px}h2{font-size:16px;margin-top:32px;border-bottom:1px solid #ddd;padding-bottom:6px}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:6px 8px;border-bottom:1px solid #eee;text-align:right}
th:first-child,td:first-child{text-align:left}th{background:#f6f6f8}
.ok{color:#0a7d33;font-weight:600}.no{color:#999}.na{color:#bbb}.warn{color:#b45309;font-weight:600}
.scroll{overflow-x:auto}table{min-width:560px}.note{font-size:12px;color:#666}.box{background:#fff8e6;border:1px solid #f0d58a;padding:10px 12px;border-radius:6px;font-size:12px}
"""


def pct(x, d=1):
    return "-" if x is None or x != x else f"{x*100:.{d}f}%"


def won(x):
    return "-" if x is None or x != x else f"{x:,.0f}원"


def spark(vals, w=100, h=24):
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    pts = " ".join(f"{i*w/(len(vals)-1):.1f},{h-(v-lo)/rng*h:.1f}" for i, v in enumerate(vals))
    return f'<svg width="{w}" height="{h}"><polyline fill="none" stroke="#3b6fd8" stroke-width="1.3" points="{pts}"/></svg>'


def table(headers, rows):
    th = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="scroll"><table><tr>{th}</tr>{body}</table></div>'


def mark(v):
    if v is None:
        return '<span class="na">n/a</span>'
    return '<span class="ok">충족</span>' if v else '<span class="no">미충족</span>'


def render(res: dict, synthetic: bool = False) -> str:
    e = html.escape
    parts = [f"<h1>일일 관찰 리포트 · {e(res['as_of'])}</h1>"]
    parts.append(
        '<div class="box">이 리포트는 조건 충족 여부를 정리한 <b>관찰·점검 자료</b>이며 매수·매도 권유나 주문이 아닙니다. '
        "세율·한도는 config.yaml 값을 사용하므로 직접 확인하세요. 과거 수익률은 미래를 보장하지 않습니다."
        + ("<br><b>⚠ 합성 데이터 모드: 아래 수치는 실제 시세가 아닙니다.</b>" if synthetic else "")
        + "</div>"
    )

    # 모의 매매
    pp = res.get("paper")
    if pp:
        parts.append("<h2>⓪ 모의 매매 (가상 계좌 · 실제 주문 없음)</h2>")
        if pp["events"]:
            parts.append("<ul>" + "".join(f"<li>{e(x)}</li>" for x in pp["events"]) + "</ul>")
        else:
            parts.append('<p class="note">오늘 새 체결·예약 없음</p>')
        bt = {}
        for s in res.get("signals", []):
            bt.setdefault(s["strategy"], []).append(s["backtest"])
        rows = []
        for s in sorted(pp["strategies"], key=lambda x: x["return"] - x["buy_hold"], reverse=True):
            b = bt.get(s["strategy"], [])
            avg = lambda k: sum(x[k] for x in b) / len(b) if b else float("nan")
            rows.append([e(s["strategy"]), str(s["slots"]), pct(s["return"]), pct(s["buy_hold"]),
                         pct(s["return"] - s["buy_hold"]), pct(avg("return")), pct(avg("buy_hold_return"))])
        parts.append("<p><b>규칙 성적표</b> (모의 매매 초과수익 순)</p>")
        parts.append(table(["규칙", "종목 수", "모의 수익률", "같은 기간 단순보유", "초과수익",
                            "과거 백테스트 평균", "과거 단순보유 평균"], rows))
        parts.append('<p class="note">판단 기준은 <b>모의 매매(앞으로의 성적)</b>입니다. 과거 백테스트는 참고용이며, '
                     '과거에 잘 맞게 규칙을 자꾸 고치면 과최적화되어 실제로는 성적이 나빠지기 쉽습니다.</p>')
        rows = [[e(r["strategy"]), e(r["name"]), e(r["state"]), e(r["started"]),
                 f"{e(r['entry_date'])} @ {r['entry_price']:,.2f}" if r["entry_price"] else "-",
                 pct(r["open_return"]), pct(r["return"]), pct(r["buy_hold"]),
                 f"{r['trades']}회 / {pct(r['win_rate'], 0)}"] for r in pp["rows"]]
        parts.append(table(["규칙", "종목", "상태", "시작일", "진입", "보유 손익", "누적", "단순보유", "완료거래/승률"], rows))
        if pp["trades"]:
            rows = [[e(t["exit_date"]), e(t["name"]), e(t["strategy"]), f"{t['entry']:,.2f} → {t['exit']:,.2f}",
                     pct(t["ret"]), e(t["reason"] or "")] for t in pp["trades"]]
            parts.append("<p><b>최근 완료 거래</b></p>" + table(["청산일", "종목", "규칙", "가격", "수익률", "사유"], rows))
        parts.append('<p class="note">가상 원금 100에서 시작해 신호 다음 거래일 종가로 체결합니다(거래비용 반영, 배당 미반영). '
                     '실행이 빠진 날은 체결이 하루씩 밀릴 수 있습니다. 실제 계좌와 무관합니다.</p>')

    # 매수·매도 조건 알림
    if res.get("signals"):
        rows = []
        for s in res["signals"]:
            b = s["backtest"]
            det = "".join(f"<div>{'▶' if v else '·'} 매수 {e(k)}</div>" for k, v in s["buy_detail"].items()) + \
                  "".join(f"<div>{'▶' if v else '·'} 매도 {e(k)}</div>" for k, v in s["sell_detail"].items())
            cls = "ok" if s["state"] == "매수 조건 충족" else ("warn" if s["state"] == "매도 조건 충족" else "no")
            rows.append([e(s["strategy"]), e(s["name"]), f'<span class="{cls}">{s["state"]}</span>',
                         f'<div style="text-align:left">{det}</div>', pct(b["return"]), pct(b["buy_hold_return"]),
                         pct(b["mdd"]), f"{b['trades']}회 / {pct(b['win_rate'], 0)}"])
        parts.append("<h2>① 내 규칙 기준 매수·매도 조건 알림</h2>")
        parts.append(table(["규칙", "종목", "오늘", "조건(▶=참)", "규칙 수익률", "단순보유", "MDD", "거래수/승률"], rows))
        parts.append('<p class="note">규칙은 config.yaml 에 사용자가 정의한 것이며 투자 권유가 아닙니다. 백테스트는 종가 신호→다음날 종가 체결, 편도 비용 가정, 같은 기간 과거 성과이며 미래를 보장하지 않습니다. 거래 횟수가 적으면 통계적 의미가 약합니다.</p>')

    # 체크리스트
    rows = []
    for c in res["checklist"]:
        cells = "".join(f"<div>{e(k)}: {mark(v)}</div>" for k, v in c["checks"].items())
        rows.append([e(c["name"]), f"{c['met']}/{c['evaluable']}", f'<div style="text-align:left">{cells}</div>',
                     f"{c['last_close']:,.0f}", spark(c["spark"])])
    parts.append("<h2>② 관찰 규칙 체크리스트 (핀트릭스·세콤달 기반)</h2>")
    parts.append(table(["종목", "충족", "조건", "종가", "최근 120일"], rows))

    # 수급
    rows = []
    for name, f in res["flows"].items():
        s = f["summary"]
        ws = sorted(s)
        cell = lambda k, w: f"{s[w][k]/1e8:,.0f}억"
        rows.append([e(name), f["state"]] + [f"{cell('foreign', w)} / {cell('institution', w)}" for w in ws])
    if res["flows"]:
        ws = sorted(next(iter(res["flows"].values()))["summary"])
        parts.append("<h2>③ 수급 (외국인 / 기관 순매수, Who Buy·세콤달 기반)</h2>")
        parts.append(table(["종목", "20일 상태"] + [f"{w}일" for w in ws], rows))

    # 밸류에이션
    v = res["valuation"]
    if isinstance(v, pd.DataFrame) and not v.empty:
        rows = [[e(str(n)), e(str(r["group"])), f"{r['per']:.1f}" if r["per"] == r["per"] else "-",
                 f"{r['pbr']:.2f}" if r["pbr"] == r["pbr"] else "-", pct(r["per_pct"], 0), pct(r["pbr_pct"], 0),
                 int(r["peers"])] for n, r in v.iterrows()]
        parts.append("<h2>④ 동종 그룹 내 밸류에이션 위치 (낮을수록 그룹 내 저평가 쪽)</h2>")
        parts.append(table(["종목", "그룹", "PER", "PBR", "PER 순위", "PBR 순위", "비교대상 수"], rows))
        parts.append('<p class="note">그룹은 config.yaml에서 직접 지정한 것입니다. 비교 대상이 4개 미만이면 순위를 표시하지 않습니다. 업종 특성상 PER/PBR만으로 저평가를 단정할 수 없습니다.</p>')

    # 총수익률
    rows = []
    for r in res["income"]:
        rows.append([e(r["name"]), pct(r["div_yield_ttm"])] + [
            f"{pct(r[f'tr_{d}']['total_return'])} <span class='note'>(가격 {pct(r[f'tr_{d}']['price_return'])})</span>"
            for d in (90, 180, 365)])
    if rows:
        parts.append("<h2>⑤ 총수익률 vs 가격변동 (센트빈 기반)</h2>")
        parts.append(table(["ETF", "분배율(TTM)", "3개월", "6개월", "1년"], rows))

    # 백테스트
    rows = [[e(b["name"]), won(b["invested"]), won(b["final_total"]), won(b["final_price_only"]),
             f"{b['multiple_total']:.2f}배", pct(b["mdd"]), pct(b["vol_annual"])] for b in res["backtests"]]
    if rows:
        parts.append("<h2>⑥ 백테스트: 일시+월 적립, 배당 재투자 (스노우볼 기반)</h2>")
        parts.append(table(["종목", "누적 투자금", "최종(배당재투자)", "최종(가격만)", "배수", "MDD", "연 변동성"], rows))

    # 괴리율
    rows = [[e(g["name"]), pct(g["premium"], 2), f"{g['z']:.2f}",
             '<span class="warn">z 경보</span>' if g["alert"] else "정상범위"] for g in res["gaps"]]
    if rows:
        parts.append("<h2>⑦ 한·미 괴리율 (데일리아카이브 기반)</h2>")
        parts.append(table(["페어", "현재 괴리율", "60일 z-score", "상태"], rows))
        parts.append('<p class="note">거래시간 차이로 종가 기준 괴리율은 시차가 있습니다. ratio(ADR당 주식 수)는 config에서 확인하세요.</p>')

    # 세금
    rows = [[e(t["name"]), won(t["est_dividend"]), won(t["general"]), won(t["isa"]), won(t["irp_now"]),
             won(t["irp_at_withdrawal"])] for t in res["tax"]]
    if rows:
        parts.append("<h2>⑧ 연 분배금 계좌별 세금 비교 (단순 추정)</h2>")
        parts.append(table(["ETF", "추정 연 분배금", "일반계좌", "ISA", "IRP(현재)", "IRP(수령 시)"], rows))

    if res["errors"]:
        parts.append("<h2>수집 오류</h2><ul>" + "".join(f"<li>{e(x)}</li>" for x in res["errors"]) + "</ul>")

    return f"<!doctype html><html lang='ko'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>일일 관찰 리포트</title><style>{CSS}</style><body>{''.join(parts)}</body></html>"


def summary_json(res: dict) -> str:
    return json.dumps(
        {"as_of": res["as_of"],
         "checklist": [{"name": c["name"], "met": c["met"], "evaluable": c["evaluable"]} for c in res["checklist"]],
         "gap_alerts": [g["name"] for g in res["gaps"] if g["alert"]],
         "paper_events": (res.get("paper") or {}).get("events", []),
         "signals": [{"strategy": s["strategy"], "name": s["name"], "state": s["state"]} for s in res.get("signals", [])],
         "flow_states": {n: f["state"] for n, f in res["flows"].items()},
         "errors": res["errors"]},
        ensure_ascii=False, indent=2)
