"""세금 계산 (데일리아카이브 양도세 계산기 / 센트빈 ISA·IRP 비교 대응).

모든 세율·한도는 config.yaml 값이며 법 개정에 따라 달라질 수 있다. 단순화된 추정치일 뿐
세무 자문이 아니다.
"""
from __future__ import annotations


def us_capital_gains_tax(realized_gain_krw: float, rate: float, deduction: float) -> float:
    """해외주식 양도소득세. 연간 손익 통산 후 기본공제 초과분에 과세."""
    taxable = max(0.0, realized_gain_krw - deduction)
    return taxable * rate


def compare_accounts(annual_dividend_krw: float, tax: dict) -> dict:
    """연 배당(분배금)에 대한 계좌별 세금 비교(단순화).

    - 일반계좌: 배당소득세
    - ISA: 비과세 한도 초과분만 분리과세
    - IRP: 인출 전까지 과세이연(연금수령 시 연금소득세) → 여기서는 수령 시 세율로 환산해 참고 표기
    """
    general = annual_dividend_krw * tax["general_div_rate"]
    isa = max(0.0, annual_dividend_krw - tax["isa_exempt_krw"]) * tax["isa_excess_rate"]
    irp_deferred = 0.0
    irp_at_withdrawal = annual_dividend_krw * tax["irp_pension_rate"]
    return {
        "general": general,
        "isa": isa,
        "irp_now": irp_deferred,
        "irp_at_withdrawal": irp_at_withdrawal,
        "isa_saving_vs_general": general - isa,
    }
