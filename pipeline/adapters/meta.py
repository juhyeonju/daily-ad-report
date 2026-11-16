"""Meta: 숫자가 문자열로 오고, 전환(purchase)이 actions 배열 안에 중첩되어 있다."""
from __future__ import annotations

import datetime as dt

import pandas as pd

from pipeline.adapters.base import MediaAdapter, empty_fact, finalize


class MetaAdapter(MediaAdapter):
    media = "meta"
    conversion_action = "purchase"

    def fetch_raw(self, report_date: dt.date) -> list[dict]:
        return [self._get("/meta/insights", date=report_date.isoformat())]

    def normalize(self, payloads: list[dict], report_date: dt.date) -> pd.DataFrame:
        rows = []
        for payload in payloads:
            for r in payload.get("data", []):
                if r["date_start"] != report_date.isoformat():
                    continue
                actions = {a["action_type"]: a["value"] for a in r.get("actions", [])}
                rows.append(
                    {
                        "campaign_id": self.campaign_id(r["campaign_id"]),
                        "spend": self.to_krw(float(r["spend"]), r.get("account_currency", "KRW")),
                        "impressions": int(r["impressions"]),
                        "clicks": int(r["clicks"]),
                        "conversions": int(float(actions.get(self.conversion_action, 0))),
                    }
                )
        return finalize(pd.DataFrame(rows), report_date) if rows else empty_fact()
