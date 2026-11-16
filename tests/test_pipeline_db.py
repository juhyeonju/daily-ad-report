"""DB 통합 테스트: 멱등성(중복 0건), 심어 둔 이상 징후 4종 탐지, 요약 fallback."""
import datetime as dt

import psycopg
import pytest
from psycopg.rows import dict_row

from mock_api import generator as g
from pipeline import db, loader, metrics, summarizer
from pipeline.collector import build_adapters, collect
from pipeline.validator import validate

END = dt.date(2026, 11, 15)
DAYS = 30
EXPECTED_CAMPAIGN = {
    "ctr_drop": "tiktok_17002",
    "overspend": "meta_23851002",
    "cpa_spike": "google_9003",
    "missing_data": "google_9004",
}


@pytest.fixture(scope="module")
def loaded(pg_url, api_client, cfg):
    conn = psycopg.connect(pg_url, row_factory=dict_row, prepare_threshold=None)
    db.init_schema(conn)
    adapters = [type(a)("http://testserver", cfg["fx"], api_client) for a in build_adapters(cfg)]
    for i in range(DAYS - 1, -1, -1):
        d = END - dt.timedelta(days=i)
        loader.load(conn, d, collect(adapters, d))
    yield conn, adapters
    conn.close()


def _count(conn):
    return conn.execute("SELECT count(*) AS n FROM fact_daily").fetchone()["n"]


def test_rerun_same_date_creates_no_duplicates(loaded):
    conn, adapters = loaded
    before = _count(conn)
    for _ in range(2):
        loader.load(conn, END, collect(adapters, END))
    assert _count(conn) == before
    dup = conn.execute(
        "SELECT count(*) AS n FROM (SELECT 1 FROM fact_daily GROUP BY report_date, campaign_id HAVING count(*) > 1) x"
    ).fetchone()["n"]
    assert dup == 0


def test_all_planted_anomalies_detected(loaded, cfg):
    """기준선이 충분한 날짜(마지막 3주)에서 심어 둔 이상 징후를 모두, 그리고 그것만 탐지."""
    conn, _ = loaded
    media = ["meta", "google", "tiktok"]
    detected_rules = set()
    for i in range(21):
        d = END - dt.timedelta(days=i)
        _, rows = metrics.build_report(conn, d, cfg, media)
        got = {(r["rule"], r["campaign_id"]) for r in rows}
        planted = {(rule, f"{m}_{cid}") for rule, (m, cid) in g.anomalies_on(d).items()}
        assert got == planted, f"{d}: got={got} planted={planted}"
        detected_rules |= {r for r, _ in got}
    assert detected_rules == set(EXPECTED_CAMPAIGN)


def test_save_alerts_is_idempotent(loaded, cfg):
    conn, _ = loaded
    d = dt.date(2026, 11, 12)
    for _ in range(2):
        _, rows = metrics.build_report(conn, d, cfg, ["meta", "google", "tiktok"])
        metrics.save_alerts(conn, d, rows)
    n = conn.execute("SELECT count(*) AS n FROM alerts WHERE report_date = %s", (d,)).fetchone()["n"]
    assert n == len(rows) == 3


def test_failed_media_excluded_from_report(loaded, cfg):
    conn, _ = loaded
    report, rows = metrics.build_report(conn, dt.date(2026, 11, 12), cfg, ["meta", "google"], ["tiktok"])
    assert {m["media"] for m in report["by_media"]} == {"Meta", "Google"}
    assert all(not r["campaign_id"].startswith("tiktok") for r in rows)
    assert report["data_issues"]


def test_template_report_passes_its_own_validation(loaded, cfg):
    conn, _ = loaded
    for i in range(21):
        report, _ = metrics.build_report(conn, END - dt.timedelta(days=i), cfg, ["meta", "google", "tiktok"])
        v = validate(summarizer.template_body(report), report)
        assert v.ok, v.feedback()


def test_compose_retries_then_falls_back(loaded, cfg, monkeypatch):
    conn, _ = loaded
    report, _ = metrics.build_report(conn, dt.date(2026, 11, 12), cfg, ["meta", "google", "tiktok"])
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    good = summarizer.template_body(report)
    answers = iter(["총 소진 1,234원", good])
    monkeypatch.setattr(summarizer, "_call_llm", lambda *a, **k: next(answers))
    c = summarizer.compose(report, cfg)
    assert (c.source, c.attempts) == ("llm", 2)

    monkeypatch.setattr(summarizer, "_call_llm", lambda *a, **k: "총 소진 1,234원")
    c = summarizer.compose(report, cfg)
    assert c.source == "template" and c.attempts == 2
