# stock_auto — 무료 웹도구 6종을 로컬 자동화 파이프라인으로

매일 장 마감 후 한 번 실행하면 관심종목의 **수급·밸류에이션·총수익률·백테스트·한미 괴리율·세금**을
하나의 HTML 리포트로 만들어 줍니다. **주문 실행·매수/매도 권유 기능은 없습니다** (관찰·점검용).

## 사이트 기능 → 모듈 대응
| 사이트 | 재구성한 기능 | 코드 |
|---|---|---|
| 센트빈 | NAV 포함 총수익률, 분배율, ISA/IRP 세금 비교 | `analytics.total_return`, `taxes.compare_accounts` |
| 스노우볼 | 배당 재투자 백테스트(일시+월 적립) | `analytics.backtest` |
| 핀트릭스 | 동종 그룹 내 PER·PBR 위치 | `analytics.valuation_percentile` |
| 데일리아카이브 | 한·미 괴리율 z-score, 양도세 계산 | `analytics.gap_scan`, `taxes.us_capital_gains_tax` |
| 세콤달.콤 / Who Buy? | 5·20·60일 외국인·기관·개인 수급 + 쏠림 판정 | `analytics.flow_summary` |

사이트를 스크래핑하지 않고 공개 라이브러리(yfinance, pykrx)로 같은 종류의 값을 직접 계산합니다.
(사이트의 이용약관·구조 변경에 영향받지 않고, 계산 로직을 직접 검증할 수 있습니다.)

## 실행
```bash
pip install -r requirements.txt
python run_daily.py --synthetic     # 오프라인 동작 확인 (가짜 데이터)
python run_daily.py                 # 실데이터
pytest -q                           # 계산 로직 테스트
```
결과: `output/report_YYYY-MM-DD.html`, `output/summary_YYYY-MM-DD.json`

## 매일 자동 실행
- macOS/Linux cron (평일 18:30): `30 18 * * 1-5 cd /경로/stock_auto && python run_daily.py`
- Windows: 작업 스케줄러 → 동작에 `python run_daily.py`, 시작 위치에 폴더 지정
- 전 종목 수집이 실패하면 종료코드 1을 반환합니다(스케줄러 실패 알림에 연결 가능).

## 설정 (`config.yaml`)
- `watchlist`: 종목과 비교 그룹(`group`)을 직접 지정. 그룹당 4개 미만이면 순위를 표시하지 않습니다.
- `rules`: 관찰 규칙 임계값. 결과는 "조건 n/m 충족" 체크리스트일 뿐 신호가 아닙니다.
- `tax`: 세율·한도. **법 개정 가능성이 있으니 본인이 확인 후 수정**하세요.
- `gap_pairs`: 예시 페어입니다. 실제 ratio(ADR당 주식 수)와 심볼을 확인해 교체하세요.

## 알아둘 한계
- `LiveProvider`는 이 환경에서 네트워크 제약으로 **실데이터 실행을 검증하지 못했습니다.** 계산 로직은
  합성 데이터와 단위 테스트로 검증했고, 첫 실행 시 yfinance/pykrx 응답 형식(컬럼명 등)을 확인하세요.
  오류는 리포트 하단 "수집 오류"에 표시됩니다.
- ETF는 PER/PBR이 없어 밸류에이션 항목이 n/a로 나옵니다.
- 백테스트는 수수료·세금·환율 변동(해외 종목)을 반영하지 않습니다. SCHD 등은 달러 가격 기준입니다.
- 한·미 괴리율은 시간대 차이로 종가 기준 시차가 있습니다.

## 다음 확장 아이디어
- 텔레그램/슬랙 알림(괴리율 z 경보, 수급 '이탈' 전환)
- DART 공시 연동, 포트폴리오 보유 현황 CSV 입력 → 리밸런싱 비중 점검
- 증권사 API 연동은 의도적으로 넣지 않았습니다(자동 주문은 오류 시 손실이 커서 별도 검토 필요).

## 클라우드 운영 (GitHub Actions + 모바일 확인)
1. GitHub에서 새 저장소 생성 → 이 폴더 내용 전체 업로드 (`.github/workflows/daily.yml` 포함)
2. Settings → Pages → Source: `Deploy from a branch`, Branch: `main`, Folder: `/docs`
3. Actions 탭 → daily-report → `Run workflow` 로 첫 실행 (이후 평일 18:30 KST 자동)
4. 폰에서 `https://<아이디>.github.io/<저장소명>/` 접속 → 홈 화면에 추가
- 주의: 무료 계정의 Pages는 **공개**입니다(관심종목이 노출됨). 비공개로 보려면 텔레그램 알림(Secrets에
  `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 등록)으로 요약만 받거나 비공개 Pages 지원 플랜을 쓰세요.
- 해외(미국) 서버에서 KRX(pykrx) 접근이 막히거나 느릴 수 있습니다. 그 경우 리포트 하단에 수집 오류로 표시됩니다.

## 매수·매도 조건 알림 (signals)
- `config.yaml` 의 `signals` 에 본인 규칙을 정의: `buy_all`(모두 참) / `sell_any`(하나라도 참) / `stop_loss` / `take_profit`
- 지원 조건: 이동평균 교차·위치(`sma_*`), RSI(`rsi_*`), 외국인+기관 수급 비율(`smart_ratio_*`)
- 같은 규칙을 과거에 돌린 결과(규칙 수익률 vs 단순보유, MDD, 거래수·승률)가 함께 표시됩니다.
  체결은 "신호 다음 거래일 종가"로 가정해 룩어헤드를 막았습니다.
- 기본 규칙 2개는 **동작 예시**입니다. 단순 규칙은 단순보유보다 못한 경우가 흔하니, 백테스트를 보고 직접 판단하세요.

## 모의 매매 (paper)
- `signals` 규칙대로 매일 가상으로 사고팔아 `docs/paper_ledger.json` 장부에 누적합니다. 실제 주문은 없습니다.
- 신호 다음 거래일 종가 체결, 거래비용 반영, 배당 미반영. 같은 날 여러 번 실행해도 한 번만 처리됩니다.
- 장부를 처음부터 다시 시작하려면 `docs/paper_ledger.json` 을 삭제하세요.
