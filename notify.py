#!/usr/bin/env python3
"""(선택) 텔레그램 요약 알림. TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 가 없으면 아무것도 하지 않는다.
PAGES_URL 이 있으면 리포트 링크를 함께 보낸다. 사용법: python notify.py [리포트폴더(기본 docs)]"""
from __future__ import annotations

import glob
import json
import os
import sys
import urllib.parse
import urllib.request


def build_message(s: dict, pages_url: str | None = None) -> str:
    lines = [f"📊 {s['as_of']} 관찰 리포트"]
    if s.get("broker"):
        b = s["broker"]
        lines.append(f"🏦 모의계좌 총평가 {b['total']:,.0f}원 ({(b['total'] / b['capital'] - 1) * 100:+.2f}%)")
    paper = s.get("paper_events") or []
    lines.append("🧪 모의 매매: " + ("\n - " + "\n - ".join(paper) if paper else "오늘 체결·예약 없음"))
    hits = [f"{x['name']}({x['strategy']}): {x['state']}" for x in s.get("signals", []) if x["state"] != "해당없음"]
    if hits:
        lines.append("📌 내 규칙 알림:\n - " + "\n - ".join(hits))
    top = [f"{c['name']} {c['met']}/{c['evaluable']}" for c in s.get("checklist", [])[:3]]
    if top:
        lines.append("체크리스트 상위: " + ", ".join(top))
    if s.get("gap_alerts"):
        lines.append("⚠ 괴리율 경보: " + ", ".join(s["gap_alerts"]))
    exits = [n for n, st in s.get("flow_states", {}).items() if st == "이탈"]
    if exits:
        lines.append("수급 이탈: " + ", ".join(exits))
    if s.get("errors"):
        lines.append(f"❗ 수집 오류 {len(s['errors'])}건 (리포트 하단 확인)")
    if pages_url:
        lines.append(pages_url + "/latest.html")
    lines.append("※ 실제 돈 주문 없음(모의계좌만) · 투자 권유 아님")
    return "\n".join(lines)


def main() -> None:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("텔레그램 설정 없음 — 알림 생략")
        return
    folder = sys.argv[1] if len(sys.argv) > 1 else "docs"
    files = sorted(glob.glob(f"{folder}/summary_*.json"))
    if not files:
        print("요약 파일 없음 — 알림 생략")
        return
    s = json.load(open(files[-1], encoding="utf-8"))
    text = build_message(s, os.environ.get("PAGES_URL"))
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=20) as r:
        print("텔레그램 전송:", r.status)


if __name__ == "__main__":
    main()
