"""Mock 매체 API 3종. 일부러 서로 다른 응답 형식을 흉내 낸다.

실행:  uvicorn mock_api.app:app --port 8000
장애 재현: MOCK_FAIL_MEDIA=tiktok uvicorn ...  → 해당 매체가 503 을 반환
"""
from __future__ import annotations

import datetime as dt
import os

from fastapi import FastAPI, HTTPException, Query

from mock_api import generator as g

app = FastAPI(title="Mock Ad Media APIs")


def _check_fail(media: str) -> None:
    failing = {m.strip() for m in os.getenv("MOCK_FAIL_MEDIA", "").split(",") if m.strip()}
    if media in failing:
        raise HTTPException(status_code=503, detail=f"{media} API temporarily unavailable (mock)")


@app.get("/health")
def health():
    return {"ok": True}


# ── 캠페인 마스터 (매체 공통 형태, 통화는 매체별) ─────────────────────
@app.get("/{media}/campaigns")
def campaigns(media: str):
    if media not in {"meta", "google", "tiktok"}:
        raise HTTPException(404, "unknown media")
    _check_fail(media)
    return {
        "data": [
            {
                "id": c.native_id,
                "name": c.name,
                "advertiser": c.advertiser,
                "daily_budget": c.daily_budget,
                "currency": c.currency,
                "status": c.status,
            }
            for c in g.campaigns_for(media)
        ]
    }


# ── Meta: 문자열 숫자, 전환은 actions 배열 안에 중첩 ─────────────────
@app.get("/meta/insights")
def meta_insights(date: dt.date = Query(..., description="KST 기준 날짜")):
    _check_fail("meta")
    rows = []
    for c in g.campaigns_for("meta"):
        t = g.daily_truth(date, c)
        if t is None:
            continue
        rows.append(
            {
                "campaign_id": c.native_id,
                "campaign_name": c.name,
                "date_start": date.isoformat(),
                "date_stop": date.isoformat(),
                "account_currency": "KRW",
                "spend": f"{t['spend']:.2f}",
                "impressions": str(t["impressions"]),
                "clicks": str(t["clicks"]),
                "actions": [
                    {"action_type": "link_click", "value": str(t["clicks"])},
                    {"action_type": "purchase", "value": str(t["conversions"])},
                ],
            }
        )
    return {"data": rows, "paging": {"cursors": {}}}


# ── Google: 비용이 micros 단위, 전환은 실수 ───────────────────────────
@app.get("/google/reports")
def google_reports(date: dt.date = Query(..., description="KST 기준 날짜")):
    _check_fail("google")
    results = []
    for c in g.campaigns_for("google"):
        t = g.daily_truth(date, c)
        if t is None:
            continue
        results.append(
            {
                "campaign": {"id": c.native_id, "name": c.name},
                "segments": {"date": date.isoformat()},
                "metrics": {
                    "cost_micros": int(t["spend"] * 1_000_000),
                    "impr": t["impressions"],
                    "clicks": t["clicks"],
                    "conversions": float(t["conversions"]),
                },
            }
        )
    return {"results": results, "currency_code": "KRW"}


# ── TikTok: UTC 기준 시간별 행, USD ─────────────────────────────────
@app.get("/tiktok/report")
def tiktok_report(date: dt.date = Query(..., description="UTC 기준 날짜")):
    _check_fail("tiktok")
    rows = []
    start = dt.datetime.combine(date, dt.time())
    for h in range(24):
        utc = start + dt.timedelta(hours=h)
        kst = utc + dt.timedelta(hours=9)
        for c in g.campaigns_for("tiktok"):
            hourly = g.tiktok_hourly(kst.date(), c.native_id)
            if hourly is None:
                continue
            m = hourly[kst.hour]
            rows.append(
                {
                    "dimensions": {"campaign_id": c.native_id, "stat_time_hour": utc.strftime("%Y-%m-%d %H:%M:%S")},
                    "metrics": {
                        "stat_cost": f"{m['stat_cost']:.2f}",
                        "show_cnt": str(m["show_cnt"]),
                        "click_cnt": str(m["click_cnt"]),
                        "convert_cnt": str(m["convert_cnt"]),
                        "currency": "USD",
                    },
                }
            )
    return {"code": 0, "message": "OK", "data": {"list": rows, "page_info": {"total_number": len(rows)}}}
