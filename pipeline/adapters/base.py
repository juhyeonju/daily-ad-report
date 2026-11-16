"""매체 Adapter 인터페이스.

새 매체(예: 네이버, 카카오)를 붙이려면 이 클래스를 상속한 파일 하나를 만들고
adapters/__init__.py 의 ADAPTERS 에 등록하면 된다. 실제 매체 API로 바꿀 때도
fetch_* 메서드만 실제 SDK 호출로 교체하면 normalize 이후 흐름은 그대로다.
"""
from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod

import httpx
import pandas as pd

# 모든 Adapter가 반환해야 하는 공통 스키마 (fact_daily 와 동일)
FACT_COLUMNS = ["report_date", "campaign_id", "spend", "impressions", "clicks", "conversions"]
CAMPAIGN_COLUMNS = ["campaign_id", "media", "name", "advertiser", "daily_budget", "is_active"]


class MediaAdapter(ABC):
    media: str = ""

    def __init__(self, base_url: str, fx: dict, client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.fx = fx
        self.client = client or httpx.Client(timeout=30)

    # ── 공통 도우미 ─────────────────────────────
    def campaign_id(self, native_id: str) -> str:
        """매체 간 ID 충돌을 막기 위해 매체 접두어를 붙인다."""
        return f"{self.media}_{native_id}"

    def to_krw(self, amount: float, currency: str) -> float:
        if currency == "KRW":
            return float(amount)
        rate = self.fx.get(f"{currency}_KRW")
        if rate is None:
            raise ValueError(f"config.yaml fx 에 {currency}_KRW 환율이 없습니다")
        return float(amount) * float(rate)

    def _get(self, path: str, **params) -> dict:
        r = self.client.get(f"{self.base_url}{path}", params=params)
        r.raise_for_status()
        return r.json()

    # ── 캠페인 마스터 ───────────────────────────
    def fetch_campaigns(self) -> pd.DataFrame:
        rows = self._get(f"/{self.media}/campaigns")["data"]
        df = pd.DataFrame(
            [
                {
                    "campaign_id": self.campaign_id(r["id"]),
                    "media": self.media,
                    "name": r["name"],
                    "advertiser": r["advertiser"],
                    "daily_budget": round(self.to_krw(r["daily_budget"], r["currency"]), 2),
                    "is_active": r["status"] == "ACTIVE",
                }
                for r in rows
            ],
            columns=CAMPAIGN_COLUMNS,
        )
        return df

    # ── 매체마다 구현 ───────────────────────────
    @abstractmethod
    def fetch_raw(self, report_date: dt.date) -> list[dict]:
        """report_date(KST) 를 만들기 위해 필요한 원본 응답들을 그대로 반환."""

    @abstractmethod
    def normalize(self, payloads: list[dict], report_date: dt.date) -> pd.DataFrame:
        """원본 응답 → FACT_COLUMNS 형태 DataFrame (KRW, KST)."""


def empty_fact() -> pd.DataFrame:
    return pd.DataFrame(columns=FACT_COLUMNS)


def finalize(df: pd.DataFrame, report_date: dt.date) -> pd.DataFrame:
    """공통 마무리: 타입 고정, 원화 반올림, 컬럼 순서."""
    if df.empty:
        return empty_fact()
    df = df.copy()
    df["report_date"] = report_date
    df["spend"] = df["spend"].astype(float).round(0)
    for col in ("impressions", "clicks", "conversions"):
        df[col] = df[col].astype(float).round(0).astype("int64")
    return df[FACT_COLUMNS].reset_index(drop=True)
