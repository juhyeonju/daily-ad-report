"""가상 광고 데이터 생성기.

- 날짜(+캠페인)를 seed로 쓰므로 같은 날짜는 언제 요청해도 같은 값이 나온다.
- 미래 날짜도 요청 시점에 만들어 주므로 cron이 매일 돌 때 "어제" 데이터가 항상 존재한다.
- 이상 징후 4종을 날짜 규칙에 따라 일부러 심는다(ANOMALIES 참고).

각 매체는 자기 통화로 보고한다: Meta/Google = KRW, TikTok = USD.
"""
from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Campaign:
    media: str
    native_id: str
    name: str
    advertiser: str
    currency: str        # 매체가 보고하는 통화
    daily_budget: float  # 매체 통화 기준
    cpm: float           # 1,000회 노출당 비용 (매체 통화)
    ctr: float
    cvr: float           # 클릭 → 전환율
    status: str = "ACTIVE"


CAMPAIGNS: list[Campaign] = [
    # Meta (KRW)
    Campaign("meta", "23851001", "겨울세일_전환", "가상패션", "KRW", 1_200_000, 6_000, 0.012, 0.035),
    Campaign("meta", "23851002", "겨울세일_리타겟팅", "가상패션", "KRW", 500_000, 9_000, 0.018, 0.050),
    Campaign("meta", "23851003", "신상품_인지도", "가상코스메틱", "KRW", 800_000, 3_500, 0.007, 0.010),
    Campaign("meta", "23851004", "브랜드_영상조회", "가상코스메틱", "KRW", 300_000, 3_000, 0.006, 0.008, "PAUSED"),
    # Google (KRW, micros 단위로 보고)
    Campaign("google", "9001", "검색_브랜드키워드", "가상패션", "KRW", 700_000, 15_000, 0.060, 0.060),
    Campaign("google", "9002", "검색_일반키워드", "가상가전", "KRW", 1_000_000, 12_000, 0.035, 0.030),
    Campaign("google", "9003", "PMax_쇼핑", "가상가전", "KRW", 900_000, 7_000, 0.015, 0.040),
    Campaign("google", "9004", "유튜브_리마케팅", "가상코스메틱", "KRW", 400_000, 5_000, 0.008, 0.020),
    # TikTok (USD, UTC 시간별로 보고)
    Campaign("tiktok", "17001", "신규설치_인피드", "가상게임", "USD", 600, 4.0, 0.011, 0.080),
    Campaign("tiktok", "17002", "신규설치_UA", "가상게임", "USD", 450, 3.5, 0.009, 0.070),
    Campaign("tiktok", "17003", "스파크애즈_리뷰", "가상코스메틱", "USD", 300, 4.5, 0.013, 0.030),
]

# 이상 징후 주입 규칙: rule -> (media, native_id, 주기(일), 오프셋)
# date.toordinal() % 주기 == 오프셋 인 날에 발생한다.
ANOMALIES: dict[str, tuple[str, str, int, int]] = {
    "ctr_drop": ("tiktok", "17002", 4, 0),       # 신규설치_UA CTR 급락
    "overspend": ("meta", "23851002", 3, 0),     # 겨울세일_리타겟팅 예산 초과 소진
    "cpa_spike": ("google", "9003", 5, 0),       # PMax_쇼핑 전환 급감 → CPA 급등
    "missing_data": ("google", "9004", 6, 0),    # 유튜브_리마케팅 데이터 누락
}

# KST 시간대별 트래픽 비중 (0시~23시)
HOURLY_WEIGHTS = [
    2, 1, 1, 1, 1, 1, 2, 3, 4, 5, 5, 5,
    6, 6, 5, 5, 5, 6, 7, 8, 9, 9, 7, 4,
]


def campaigns_for(media: str) -> list[Campaign]:
    return [c for c in CAMPAIGNS if c.media == media]


def anomalies_on(date: dt.date) -> dict[str, tuple[str, str]]:
    """그날 발생하는 이상 징후 {rule: (media, native_id)}"""
    o = date.toordinal()
    return {rule: (m, cid) for rule, (m, cid, period, off) in ANOMALIES.items() if o % period == off}


def _has(date: dt.date, rule: str, c: Campaign) -> bool:
    hit = anomalies_on(date).get(rule)
    return hit == (c.media, c.native_id)


def daily_truth(date: dt.date, c: Campaign) -> dict | None:
    """캠페인의 KST 하루 성과(매체 통화). 데이터가 없는 날은 None."""
    if c.status != "ACTIVE" or _has(date, "missing_data", c):
        return None

    rng = random.Random(f"{date.isoformat()}|{c.media}|{c.native_id}")
    weekend = date.weekday() >= 5

    spend = c.daily_budget * rng.uniform(0.78, 0.97)
    if _has(date, "overspend", c):
        spend = c.daily_budget * rng.uniform(1.30, 1.45)

    cpm = c.cpm * rng.uniform(0.92, 1.08) * (0.95 if weekend else 1.0)
    impressions = int(spend / cpm * 1000)

    ctr = c.ctr * rng.uniform(0.92, 1.08)
    normal_clicks = impressions * ctr
    ctr_drop = _has(date, "ctr_drop", c)
    if ctr_drop:
        ctr *= 0.40
    clicks = int(round(impressions * ctr))

    cvr = c.cvr * rng.uniform(0.88, 1.12)
    # CTR 급락일에도 전환은 크게 줄지 않게 해서 ctr_drop 과 cpa_spike 가 겹치지 않도록 함
    conversions = int(round((normal_clicks * 0.9 if ctr_drop else clicks) * cvr))
    if _has(date, "cpa_spike", c):
        conversions = int(round(conversions * 0.40))

    spend = round(spend) if c.currency == "KRW" else round(spend, 2)
    return {"spend": spend, "impressions": impressions, "clicks": clicks, "conversions": conversions}


def _allocate(total: int, weights: list[int]) -> list[int]:
    """정수 total 을 weights 비율로 나누되 합계가 정확히 total 이 되게 (최대 잉여 방식)."""
    s = sum(weights)
    raw = [total * w / s for w in weights]
    base = [int(x) for x in raw]
    remain = total - sum(base)
    order = sorted(range(len(raw)), key=lambda i: (raw[i] - base[i], -i), reverse=True)
    for i in order[:remain]:
        base[i] += 1
    return base


@lru_cache(maxsize=4096)
def tiktok_hourly(kst_date: dt.date, native_id: str) -> tuple[dict, ...] | None:
    """TikTok 캠페인의 KST 시간별 성과 24개. 비용은 USD."""
    c = next(x for x in CAMPAIGNS if x.media == "tiktok" and x.native_id == native_id)
    t = daily_truth(kst_date, c)
    if t is None:
        return None
    cents = _allocate(int(round(t["spend"] * 100)), HOURLY_WEIGHTS)
    impr = _allocate(t["impressions"], HOURLY_WEIGHTS)
    clicks = _allocate(t["clicks"], HOURLY_WEIGHTS)
    conv = _allocate(t["conversions"], HOURLY_WEIGHTS)
    return tuple(
        {"stat_cost": cents[h] / 100, "show_cnt": impr[h], "click_cnt": clicks[h], "convert_cnt": conv[h]}
        for h in range(24)
    )


def anomaly_calendar(start: dt.date, days: int) -> list[tuple[dt.date, list[str]]]:
    """디버깅용: 기간 내 날짜별로 어떤 이상 징후가 심어져 있는지."""
    return [(start + dt.timedelta(d), sorted(anomalies_on(start + dt.timedelta(d)))) for d in range(days)]


if __name__ == "__main__":
    import sys

    days = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    today = dt.date.today()
    for d, rules in anomaly_calendar(today - dt.timedelta(days), days):
        print(d, d.strftime("%a"), ", ".join(rules) or "-")
