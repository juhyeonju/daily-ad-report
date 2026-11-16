import datetime as dt

import pytest

from mock_api import generator as g
from pipeline.adapters import ADAPTERS
from pipeline.adapters.tiktok import TikTokAdapter

D = dt.date(2026, 11, 12)
FX = {"USD_KRW": 1400}


def _adapter(media, api_client):
    return ADAPTERS[media]("http://testserver", FX, api_client)


@pytest.mark.parametrize("media", ["meta", "google"])
def test_krw_media_matches_generator(media, api_client):
    a = _adapter(media, api_client)
    df = a.normalize(a.fetch_raw(D), D).set_index("campaign_id")
    for c in g.campaigns_for(media):
        truth = g.daily_truth(D, c)
        cid = f"{media}_{c.native_id}"
        if truth is None:
            assert cid not in df.index
            continue
        row = df.loc[cid]
        assert row.spend == truth["spend"]
        assert row.impressions == truth["impressions"]
        assert row.clicks == truth["clicks"]
        assert row.conversions == truth["conversions"]


def test_google_cost_micros_converted(api_client):
    a = _adapter("google", api_client)
    raw = a.fetch_raw(D)[0]["results"][0]
    df = a.normalize(a.fetch_raw(D), D)
    cid = "google_" + raw["campaign"]["id"]
    assert df.set_index("campaign_id").loc[cid].spend == raw["metrics"]["cost_micros"] / 1_000_000


def test_meta_conversions_from_nested_actions(api_client):
    a = _adapter("meta", api_client)
    payload = {"data": [{
        "campaign_id": "1", "date_start": D.isoformat(), "account_currency": "KRW",
        "spend": "1000.00", "impressions": "100", "clicks": "10",
        "actions": [{"action_type": "link_click", "value": "10"}, {"action_type": "purchase", "value": "3"}],
    }]}
    row = a.normalize([payload], D).iloc[0]
    assert (row.campaign_id, row.conversions) == ("meta_1", 3)


def test_tiktok_reaggregates_utc_hours_to_kst_day(api_client):
    """UTC 시간별 USD → KST 하루 KRW 합계가 생성기의 KST 하루 값과 같아야 한다."""
    a = _adapter("tiktok", api_client)
    df = a.normalize(a.fetch_raw(D), D).set_index("campaign_id")
    for c in g.campaigns_for("tiktok"):
        truth = g.daily_truth(D, c)
        row = df.loc[f"tiktok_{c.native_id}"]
        assert row.impressions == truth["impressions"]
        assert row.clicks == truth["clicks"]
        assert row.conversions == truth["conversions"]
        assert row.spend == round(truth["spend"] * FX["USD_KRW"])


def test_tiktok_timezone_boundary():
    """UTC 전날 15:00 은 KST 당일 00:00 → 포함, UTC 전날 14:00 은 KST 전날 23:00 → 제외."""
    a = TikTokAdapter("http://unused", FX)

    def row(ts, cost):
        return {"dimensions": {"campaign_id": "1", "stat_time_hour": ts},
                "metrics": {"stat_cost": cost, "show_cnt": "10", "click_cnt": "1", "convert_cnt": "0"}}

    payload = {"data": {"list": [
        row("2026-11-11 14:00:00", "1.00"),   # KST 11/11 23:00 → 제외
        row("2026-11-11 15:00:00", "2.00"),   # KST 11/12 00:00 → 포함
        row("2026-11-12 14:00:00", "3.00"),   # KST 11/12 23:00 → 포함
        row("2026-11-12 15:00:00", "4.00"),   # KST 11/13 00:00 → 제외
    ]}}
    out = a.normalize([payload], D).iloc[0]
    assert out.spend == 5.00 * 1400
    assert out.impressions == 20


def test_campaign_master_budget_in_krw(api_client):
    df = _adapter("tiktok", api_client).fetch_campaigns().set_index("campaign_id")
    assert df.loc["tiktok_17002"].daily_budget == 450 * 1400
    meta = _adapter("meta", api_client).fetch_campaigns().set_index("campaign_id")
    assert not meta.loc["meta_23851004"].is_active   # PAUSED


def test_same_date_is_deterministic(api_client):
    a = _adapter("tiktok", api_client)
    assert a.fetch_raw(D) == a.fetch_raw(D)
