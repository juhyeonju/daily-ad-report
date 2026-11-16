from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
KST = ZoneInfo("Asia/Seoul")


def load_config(path: str | Path | None = None) -> dict:
    path = Path(path or os.getenv("REPORT_CONFIG", ROOT / "config.yaml"))
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def yesterday_kst() -> dt.date:
    return dt.datetime.now(KST).date() - dt.timedelta(days=1)


def mock_base_url() -> str:
    return os.getenv("MOCK_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
