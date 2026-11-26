#!/usr/bin/env python3
"""docs/ 폴더의 report_*.html 을 모아 모바일용 목록 페이지(index.html)를 만든다.
최신 리포트는 latest.html 로도 복사해 두 번 탭하지 않아도 열리게 한다."""
from __future__ import annotations

import pathlib
import shutil
import sys


def main(folder: str = "docs") -> None:
    d = pathlib.Path(folder)
    reports = sorted(d.glob("report_*.html"), reverse=True)
    if not reports:
        print("리포트 없음", file=sys.stderr)
        raise SystemExit(1)
    shutil.copyfile(reports[0], d / "latest.html")
    items = "".join(
        f'<li><a href="{r.name}">{r.stem.replace("report_", "")}</a></li>' for r in reports[:120]
    )
    (d / "index.html").write_text(
        "<!doctype html><html lang='ko'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'><title>일일 관찰 리포트</title>"
        "<style>body{font-family:-apple-system,'Malgun Gothic',sans-serif;max-width:520px;margin:24px auto;padding:0 16px}"
        "a{display:block;padding:14px 0;border-bottom:1px solid #eee;font-size:17px;text-decoration:none}"
        ".top{background:#3b6fd8;color:#fff;border-radius:8px;padding:16px;text-align:center;margin:16px 0}</style>"
        f"<body><h1>일일 관찰 리포트</h1><a class='top' href='latest.html' style='color:#fff'>최신 리포트 열기 ({reports[0].stem[7:]})</a>"
        f"<ul style='list-style:none;padding:0'>{items}</ul></body></html>",
        encoding="utf-8",
    )
    print(f"index.html 생성 ({len(reports)}건)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "docs")
