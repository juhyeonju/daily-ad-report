"""TikTok: UTC 기준 시간별 행, USD.

KST 하루(D 00:00~23:59)는 UTC로 D-1 15:00 ~ D 14:59 이므로
UTC 날짜 D-1, D 두 번을 조회한 뒤 +9시간 해서 KST 날짜가 D인 행만 합산한다.
(UTC 일별 합계만 받으면 KST 하루로 정확히 바꿀 수 없기 때문)
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from pipeline.adapters.base import MediaAdapter, empty_fact, finalize

KST_OFFSET = pd.Timedelta(hours=9)


class TikTokAdapter(MediaAdapter):
    media = "tiktok"

    def fetch_raw(self, report_date: dt.date) -> list[dict]:
        utc_dates = [report_date - dt.timedelta(days=1), report_date]
        return [self._get("/tiktok/report", date=d.isoformat()) for d in utc_dates]

    def normalize(self, payloads: list[dict], report_date: dt.date) -> pd.DataFrame:
        records = []
        for payload in payloads:
            for r in payload.get("data", {}).get("list", []):
                m = r["metrics"]
                records.append(
                    {
                        "native_id": r["dimensions"]["campaign_id"],
                        "utc_hour": r["dimensions"]["stat_time_hour"],
                        "cost": float(m["stat_cost"]),
                        "currency": m.get("currency", "USD"),
                        "impressions": int(m["show_cnt"]),
                        "clicks": int(m["click_cnt"]),
                        "conversions": int(m["convert_cnt"]),
                    }
                )
        if not records:
            return empty_fact()

        df = pd.DataFrame(records)
        df["kst_date"] = (pd.to_datetime(df["utc_hour"], utc=True) + KST_OFFSET).dt.date
        df = df[df["kst_date"] == report_date]
        # 두 UTC 날짜 응답이 겹치는 시간은 없지만, 재요청 등으로 중복된 행이 있으면 제거
        df = df.drop_duplicates(subset=["native_id", "utc_hour"])
        if df.empty:
            return empty_fact()

        if df["currency"].nunique() != 1:
            raise ValueError("TikTok 응답에 통화가 섞여 있습니다")
        currency = df["currency"].iloc[0]

        # 통화 변환은 합산 후 1번만 (시간별로 반올림 오차가 쌓이지 않게)
        agg = df.groupby("native_id", as_index=False)[["cost", "impressions", "clicks", "conversions"]].sum()
        agg["campaign_id"] = agg["native_id"].map(self.campaign_id)
        agg["spend"] = agg["cost"].map(lambda v: self.to_krw(v, currency))
        return finalize(agg, report_date)
