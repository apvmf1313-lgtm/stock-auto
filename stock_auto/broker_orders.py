#!/usr/bin/env python3
"""아침(09:05) 실행: 전날 저녁 가상 장부가 예약한 주문을 한국투자 **모의투자** 계좌에 시장가로 넣는다.

  python broker_orders.py --docs ../docs
필요 환경변수: KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO (선택: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import pathlib
import sys
import urllib.parse
import urllib.request

from stockauto import broker as bk
from stockauto import paper, pipeline
from stockauto.providers import KisProvider


def telegram(text: str) -> None:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=20)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--docs", default="../docs")
    args = ap.parse_args()
    cfg = pipeline.load_config(args.config)
    if not cfg.get("broker", {}).get("enabled"):
        print("broker.enabled=false — 주문 생략")
        return 0
    need = ["KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT_NO"]
    if any(not os.environ.get(k) for k in need):
        print("KIS 키 또는 계좌번호 없음 — 주문 생략")
        return 0
    docs = pathlib.Path(args.docs)
    paper_led = paper.load(docs / "paper_ledger.json")
    bled_path = docs / "broker_ledger.json"
    bled = bk.load(bled_path)

    actions = bk.plan(cfg, paper_led, bled)
    if not actions:
        print("오늘 넣을 주문 없음")
        bk.save(bled, bled_path)
        telegram("🛒 모의투자 계좌: 오늘 넣을 주문 없음 (어제 저녁 예약된 매수·매도 신호 없음, 정상 실행)")
        return 0
    kis = KisProvider(os.environ["KIS_APP_KEY"], os.environ["KIS_APP_SECRET"], env=cfg.get("kis", {}).get("env", "demo"))
    broker = bk.MockBroker(kis, os.environ["KIS_ACCOUNT_NO"])
    today = dt.date.today().isoformat()
    try:
        msgs = bk.execute(actions, broker, bled, today)
    finally:
        bk.save(bled, bled_path)          # 실패해도 그때까지의 기록은 남긴다
    for m in msgs:
        print(m)
    if msgs:
        telegram("🛒 모의투자 계좌 주문 (가상 돈)\n" + "\n".join(msgs))
    return 1 if any(m.startswith("❌") for m in msgs) else 0


if __name__ == "__main__":
    sys.exit(main())
