"""DB 적재. fact_daily 는 (report_date, campaign_id) 기준 upsert → 같은 날짜를 다시 돌려도 중복 없음."""
from __future__ import annotations

import datetime as dt

import psycopg
from psycopg.types.json import Jsonb

from pipeline.collector import MediaResult

UPSERT_CAMPAIGN = """
INSERT INTO campaigns (campaign_id, media, name, advertiser, daily_budget, is_active, updated_at)
VALUES (%(campaign_id)s, %(media)s, %(name)s, %(advertiser)s, %(daily_budget)s, %(is_active)s, now())
ON CONFLICT (campaign_id) DO UPDATE SET
  name = EXCLUDED.name, advertiser = EXCLUDED.advertiser, daily_budget = EXCLUDED.daily_budget,
  is_active = EXCLUDED.is_active, updated_at = now()
"""

UPSERT_FACT = """
INSERT INTO fact_daily (report_date, campaign_id, spend, impressions, clicks, conversions, loaded_at)
VALUES (%(report_date)s, %(campaign_id)s, %(spend)s, %(impressions)s, %(clicks)s, %(conversions)s, now())
ON CONFLICT (report_date, campaign_id) DO UPDATE SET
  spend = EXCLUDED.spend, impressions = EXCLUDED.impressions, clicks = EXCLUDED.clicks,
  conversions = EXCLUDED.conversions, loaded_at = now()
"""


def _records(df) -> list[dict]:
    # numpy 타입 → 파이썬 기본 타입 (psycopg 가 이해하도록)
    return [{k: (v.item() if hasattr(v, "item") else v) for k, v in r.items()} for r in df.to_dict("records")]


def load(conn: psycopg.Connection, report_date: dt.date, results: list[MediaResult]) -> dict:
    """성공한 매체만 적재. 매체별 적재 행 수를 반환."""
    loaded = {}
    with conn.cursor() as cur:
        for res in results:
            if not res.ok:
                continue
            if res.campaigns is not None and not res.campaigns.empty:
                cur.executemany(UPSERT_CAMPAIGN, _records(res.campaigns))
            cur.execute(
                "INSERT INTO raw_responses (media, report_date, payload) VALUES (%s, %s, %s)",
                (res.media, report_date, Jsonb({"responses": res.payloads})),
            )
            if res.facts is not None and not res.facts.empty:
                cur.executemany(UPSERT_FACT, _records(res.facts))
            loaded[res.media] = 0 if res.facts is None else len(res.facts)
    conn.commit()
    return loaded


def dates_with_data(conn: psycopg.Connection, start: dt.date, end: dt.date) -> set[dt.date]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT report_date FROM fact_daily WHERE report_date BETWEEN %s AND %s", (start, end)
        )
        return {r["report_date"] for r in cur.fetchall()}
