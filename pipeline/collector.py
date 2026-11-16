"""매체별 Adapter를 돌려 원본·정규화 데이터를 모은다. 한 매체가 실패해도 나머지는 계속 진행."""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

import httpx
import pandas as pd

from pipeline.adapters import ADAPTERS
from pipeline.config import mock_base_url

log = logging.getLogger(__name__)


@dataclass
class MediaResult:
    media: str
    ok: bool
    campaigns: pd.DataFrame | None = None
    payloads: list[dict] = field(default_factory=list)
    facts: pd.DataFrame | None = None
    error: str | None = None


def build_adapters(cfg: dict, client: httpx.Client | None = None) -> list:
    base = mock_base_url()
    client = client or httpx.Client(timeout=30)
    enabled = [m for m, opt in cfg["media"].items() if opt.get("enabled", True)]
    return [ADAPTERS[m](base, cfg["fx"], client) for m in enabled]


def collect(adapters: list, report_date: dt.date, with_campaigns: bool = True) -> list[MediaResult]:
    results = []
    for a in adapters:
        try:
            campaigns = a.fetch_campaigns() if with_campaigns else None
            payloads = a.fetch_raw(report_date)
            facts = a.normalize(payloads, report_date)
            results.append(MediaResult(a.media, True, campaigns, payloads, facts))
            log.info("collected %s %s: %d rows", a.media, report_date, len(facts))
        except Exception as e:  # 매체 하나의 장애가 전체를 멈추지 않도록
            log.warning("collect failed %s %s: %s", a.media, report_date, e)
            results.append(MediaResult(a.media, False, error=f"{type(e).__name__}: {e}"))
    return results
