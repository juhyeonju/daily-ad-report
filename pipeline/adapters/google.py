"""Google: 비용이 micros 단위(÷1,000,000), 노출 필드명이 impr, 전환이 실수."""
from __future__ import annotations

import datetime as dt

import pandas as pd

from pipeline.adapters.base import MediaAdapter, empty_fact, finalize

MICROS = 1_000_000


class GoogleAdapter(MediaAdapter):
    media = "google"

    def fetch_raw(self, report_date: dt.date) -> list[dict]:
        return [self._get("/google/reports", date=report_date.isoformat())]

    def normalize(self, payloads: list[dict], report_date: dt.date) -> pd.DataFrame:
        rows = []
        for payload in payloads:
            currency = payload.get("currency_code", "KRW")
            for r in payload.get("results", []):
                if r["segments"]["date"] != report_date.isoformat():
                    continue
                m = r["metrics"]
                rows.append(
                    {
                        "campaign_id": self.campaign_id(r["campaign"]["id"]),
                        "spend": self.to_krw(int(m["cost_micros"]) / MICROS, currency),
                        "impressions": int(m["impr"]),
                        "clicks": int(m["clicks"]),
                        "conversions": round(float(m["conversions"])),
                    }
                )
        return finalize(pd.DataFrame(rows), report_date) if rows else empty_fact()
