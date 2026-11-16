from decimal import Decimal

from pipeline.validator import extract, validate

REPORT = {
    "total": {"spend": "4,820,000원", "spend_change": "+12.4%", "conversions": "312건", "cpa": "15,449원"},
    "by_media": [{"media": "Meta", "spend_share": "52%", "ctr": "0.42%"}],
    "alerts": [{"campaign": "겨울세일_리타겟팅", "description": "겨울세일_리타겟팅(Meta) 예산 대비 138% 소진"}],
    "alert_count": "1건",
}


def test_extract_units_and_multipliers():
    vals = {(v, u) for _, v, u in extract("총 482만원, +12.4%, 312건, 1.2억원, 7일 평균, 11/15")}
    assert vals == {
        (Decimal("4820000"), "원"), (Decimal("12.4"), "%"), (Decimal("312"), "건"), (Decimal("120000000"), "원"),
    }


def test_valid_summary_passes():
    s = ("총 소진 4,820,000원 (전일 대비 +12.4%) · 전환 312건 · CPA 15,449원\n"
         "⚠️ 확인 필요\n• [critical] 겨울세일_리타겟팅(Meta) 예산 대비 138% 소진\n"
         "매체별\n• Meta: 소진 비중 52%, 7일 평균 대비 확인 필요")
    r = validate(s, REPORT)
    assert r.ok, r.feedback()
    assert r.checked == 6


def test_sign_written_in_words_is_ok():
    assert validate("소진 12.4% 증가, 겨울세일_리타겟팅 확인 필요", REPORT).ok


def test_made_up_number_fails():
    r = validate("총 소진 4,830,000원, 겨울세일_리타겟팅 확인", REPORT)
    assert not r.ok and r.mismatched == ["4,830,000원"]


def test_rounded_unit_conversion_fails():
    # 4,820,000원 → "482만원" 은 값이 같아 통과, "약 480만원" 은 값이 달라 실패
    assert validate("총 482만원, 겨울세일_리타겟팅", REPORT).ok
    assert not validate("총 약 480만원, 겨울세일_리타겟팅", REPORT).ok


def test_rounded_percent_fails():
    assert not validate("CTR 0.4%, 겨울세일_리타겟팅", REPORT).ok


def test_missing_alert_fails():
    r = validate("총 소진 4,820,000원", REPORT)
    assert not r.ok and r.missing_alerts == ["겨울세일_리타겟팅"]


def test_empty_summary_fails():
    assert not validate("   ", {"alerts": []}).ok
