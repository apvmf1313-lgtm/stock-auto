#!/usr/bin/env python3
"""(선택) 텔레그램 요약 알림. TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 가 없으면 아무것도 하지 않는다.
PAGES_URL 이 있으면 리포트 링크를 함께 보낸다."""
from __future__ import annotations

import glob
import json
import os
import urllib.parse
import urllib.request


def main() -> None:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("텔레그램 설정 없음 — 알림 생략")
        return
    files = sorted(glob.glob("docs/summary_*.json"))
    if not files:
        return
    s = json.load(open(files[-1], encoding="utf-8"))
    top = [f"{c['name']} {c['met']}/{c['evaluable']}" for c in s["checklist"][:3]]
    lines = [f"📊 {s['as_of']} 관찰 리포트", "조건 충족 상위: " + ", ".join(top)]
    hits = [f"{x['name']}({x['strategy']}): {x['state']}" for x in s.get("signals", []) if x["state"] != "해당없음"]
    if hits:
        lines.append("내 규칙 알림: " + "; ".join(hits))
    if s["gap_alerts"]:
        lines.append("⚠ 괴리율 경보: " + ", ".join(s["gap_alerts"]))
    exits = [n for n, st in s["flow_states"].items() if st == "이탈"]
    if exits:
        lines.append("수급 이탈: " + ", ".join(exits))
    if s["errors"]:
        lines.append(f"수집 오류 {len(s['errors'])}건")
    if os.environ.get("PAGES_URL"):
        lines.append(os.environ["PAGES_URL"] + "/latest.html")
    data = urllib.parse.urlencode({"chat_id": chat, "text": "\n".join(lines)}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=20)


if __name__ == "__main__":
    main()
