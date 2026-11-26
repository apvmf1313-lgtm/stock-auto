#!/usr/bin/env python3
"""일일 실행 진입점.

  python run_daily.py                    # 실데이터(yfinance+pykrx)
  python run_daily.py --synthetic        # 오프라인 합성 데이터로 동작 확인
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys

from stockauto import pipeline, report
from stockauto.providers import LiveProvider, SyntheticProvider


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--synthetic", action="store_true", help="가상 데이터(실제 시세 아님)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    cfg = pipeline.load_config(args.config)
    provider = SyntheticProvider() if args.synthetic else LiveProvider()
    res = pipeline.run(cfg, provider)

    out = pathlib.Path(args.out or cfg["report"]["output_dir"])
    out.mkdir(parents=True, exist_ok=True)
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
