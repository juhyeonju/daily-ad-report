from pipeline.adapters.base import FACT_COLUMNS, MediaAdapter
from pipeline.adapters.google import GoogleAdapter
from pipeline.adapters.meta import MetaAdapter
from pipeline.adapters.tiktok import TikTokAdapter

# 새 매체를 추가하면 여기에 한 줄 등록
ADAPTERS: dict[str, type[MediaAdapter]] = {
    "meta": MetaAdapter,
    "google": GoogleAdapter,
    "tiktok": TikTokAdapter,
}

MEDIA_LABELS = {"meta": "Meta", "google": "Google", "tiktok": "TikTok"}

__all__ = ["ADAPTERS", "MEDIA_LABELS", "FACT_COLUMNS", "MediaAdapter"]
