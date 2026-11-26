#!/usr/bin/env python3
"""일일 실행 진입점.

  python run_daily.py                    # 실데이터(yfinance+pykrx)
  python run_daily.py --synthetic        # 오프라인 합성 데이터로 동작 확인
"""
from __future__ import annotations

import argparse
import os
import datetime as dt
import pathlib
import sys

from stockauto import broker as bk
from stockauto import paper, pipeline, report
from stockauto.providers import KisProvider, LiveProvider, SyntheticProvider


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--synthetic", action="store_true", help="가상 데이터(실제 시세 아님)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    cfg = pipeline.load_config(args.config)
    out = pathlib.Path(args.out or cfg["report"]["output_dir"])
    out.mkdir(parents=True, exist_ok=True)
    if args.synthetic:
        provider = SyntheticProvider()
    elif os.environ.get("KIS_APP_KEY") and os.environ.get("KIS_APP_SECRET"):
        provider = KisProvider(os.environ["KIS_APP_KEY"], os.environ["KIS_APP_SECRET"],
                               env=cfg.get("kis", {}).get("env", "demo"), cache_dir=out / "flows")
        print(f"수급·PER/PBR: 한국투자증권 Open API ({provider.base})")
    else:
        provider = LiveProvider()
        print("수급·PER/PBR: pykrx (KIS_APP_KEY 없음)")
    # 모의 매매 장부: 리포트 폴더에 저장되어 다음 실행에 이어진다 (합성 모드는 별도 파일)
    ledger_path = out / ("paper_ledger_synthetic.json" if args.synthetic else "paper_ledger.json")
    ledger = paper.load(ledger_path)
    res = pipeline.run(cfg, provider, ledger=ledger)
    paper.save(ledger, ledger_path)
    # 한국투자 모의 계좌 현황 (주문은 하지 않고 조회만)
    if isinstance(provider, KisProvider) and os.environ.get("KIS_ACCOUNT_NO") and cfg.get("broker", {}).get("enabled"):
        try:
            mb = bk.MockBroker(provider, os.environ["KIS_ACCOUNT_NO"])
            bled = bk.load(out / "broker_ledger.json")
            res["broker"] = {"balance": mb.balance(), "capital": cfg["broker"]["capital_krw"],
                             "strategies": cfg["broker"]["strategies"], "orders": bled["orders"][-15:][::-1]}
        except Exception as e:
            res["errors"].append(f"모의 계좌 조회 실패: {e}")
    stamp = dt.date.today().isoformat()
    (out / f"report_{stamp}.html").write_text(report.render(res, args.synthetic), encoding="utf-8")
    (out / f"summary_{stamp}.json").write_text(report.summary_json(res), encoding="utf-8")
    print(f"저장: {out}/report_{stamp}.html")
    for err in res["errors"]:
        print("경고:", err, file=sys.stderr)
    # 일부 종목만 실패한 경우는 정상 종료, 전부 실패하면 비정상 종료(스케줄러가 감지)
    return 1 if not res["checklist"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
