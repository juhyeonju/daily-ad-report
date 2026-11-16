"""요약문 검증.

1) 요약문에서 단위가 붙은 숫자(원, %, 건)를 뽑아 리포트 JSON에 있는 값과 대조한다.
   - "482만원" 처럼 만/억 단위로 바꿔 쓴 것도 값으로 환산해 비교한다.
   - 부호(+/-)는 "12.4% 증가"처럼 말로 풀어 쓰는 경우가 있어 절댓값으로 비교한다.
   - 날짜(11/15), "7일 평균"처럼 단위가 원/%/건이 아닌 숫자는 대상이 아니다.
2) 리포트의 모든 경보 캠페인이 요약문에 언급되었는지 확인한다(경보 누락 방지).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

NUM_RE = re.compile(
    r"(?<![\d.,])[+\-−]?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*(만|억)?\s*(원|%|건)"
)
MULTIPLIER = {None: 1, "만": 10_000, "억": 100_000_000}


@dataclass
class ValidationResult:
    ok: bool
    mismatched: list[str] = field(default_factory=list)   # JSON에 없는 숫자
    missing_alerts: list[str] = field(default_factory=list)  # 언급 안 된 경보 캠페인
    checked: int = 0

    def feedback(self) -> str:
        parts = []
        if self.mismatched:
            parts.append("JSON에 없는 숫자: " + ", ".join(self.mismatched))
        if self.missing_alerts:
            parts.append("빠진 경보 캠페인: " + ", ".join(self.missing_alerts))
        return " / ".join(parts)


def extract(text: str) -> list[tuple[str, Decimal, str]]:
    """[(원문, 값, 단위)]"""
    out = []
    for m in NUM_RE.finditer(text):
        integer, frac, mult, unit = m.groups()
        value = Decimal(integer.replace(",", "") + (frac or "")) * MULTIPLIER[mult]
        out.append((m.group(0).strip(), value.normalize(), unit))
    return out


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def allowed_values(report: dict) -> set[tuple[Decimal, str]]:
    return {(v, unit) for s in _strings(report) for _, v, unit in extract(s)}


def validate(summary: str, report: dict) -> ValidationResult:
    allowed = allowed_values(report)
    found = extract(summary)
    mismatched = [raw for raw, v, unit in found if (v, unit) not in allowed]
    missing = sorted({a["campaign"] for a in report.get("alerts", []) if a["campaign"] not in summary})
    ok = bool(summary.strip()) and not mismatched and not missing
    return ValidationResult(ok, mismatched, missing, checked=len(found))
