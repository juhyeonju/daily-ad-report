"""지표 계산과 이상 탐지. 모든 숫자는 SQL에서 계산하고, 파이썬은 표기용 문자열만 만든다."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import psycopg

from pipeline.adapters import MEDIA_LABELS

# ── SQL ───────────────────────────────────────────────────────────
# 매체별·전체 합계와 전일 대비 변화율 (ROLLUP 의 media IS NULL 행이 전체 합계)
MEDIA_SQL = """
WITH m AS (
  SELECT c.media,
    SUM(f.spend)       FILTER (WHERE f.report_date = %(d)s)     AS spend,
    SUM(f.spend)       FILTER (WHERE f.report_date = %(d)s - 1) AS prev_spend,
    SUM(f.impressions) FILTER (WHERE f.report_date = %(d)s)     AS impressions,
    SUM(f.impressions) FILTER (WHERE f.report_date = %(d)s - 1) AS prev_impressions,
    SUM(f.clicks)      FILTER (WHERE f.report_date = %(d)s)     AS clicks,
    SUM(f.clicks)      FILTER (WHERE f.report_date = %(d)s - 1) AS prev_clicks,
    SUM(f.conversions) FILTER (WHERE f.report_date = %(d)s)     AS conversions,
    SUM(f.conversions) FILTER (WHERE f.report_date = %(d)s - 1) AS prev_conversions
  FROM fact_daily f JOIN campaigns c USING (campaign_id)
  WHERE f.report_date IN (%(d)s, %(d)s - 1) AND c.media = ANY(%(media)s)
  GROUP BY ROLLUP (c.media)
), k AS (
  SELECT m.*,
    spend / NULLIF(conversions, 0)                 AS cpa,
    prev_spend / NULLIF(prev_conversions, 0)       AS prev_cpa,
    clicks::numeric / NULLIF(impressions, 0)       AS ctr,
    prev_clicks::numeric / NULLIF(prev_impressions, 0) AS prev_ctr
  FROM m
)
SELECT k.*,
  (spend - prev_spend) / NULLIF(prev_spend, 0) * 100                         AS spend_chg,
  (impressions - prev_impressions)::numeric / NULLIF(prev_impressions, 0) * 100 AS impressions_chg,
  (clicks - prev_clicks)::numeric / NULLIF(prev_clicks, 0) * 100             AS clicks_chg,
  (conversions - prev_conversions)::numeric / NULLIF(prev_conversions, 0) * 100 AS conversions_chg,
  (cpa - prev_cpa) / NULLIF(prev_cpa, 0) * 100                               AS cpa_chg,
  (ctr - prev_ctr) / NULLIF(prev_ctr, 0) * 100                               AS ctr_chg,
  spend / NULLIF(SUM(spend) FILTER (WHERE media IS NOT NULL) OVER (), 0) * 100 AS spend_share
FROM k
ORDER BY media NULLS FIRST
"""

# 전일 vs 최근 N일(전일 제외) 기준선. 기준선 CTR/CPA는 일별 비율의 평균이 아니라 합계의 비율.
BASELINE_CTE = """
base AS (
  SELECT campaign_id,
         COUNT(*)                                   AS days,
         SUM(clicks)::numeric / NULLIF(SUM(impressions), 0) AS ctr,
         SUM(spend) / NULLIF(SUM(conversions), 0)   AS cpa
  FROM fact_daily
  WHERE report_date BETWEEN %(d)s - %(n)s AND %(d)s - 1
  GROUP BY campaign_id
),
cur AS (SELECT * FROM v_daily_kpi WHERE report_date = %(d)s),
camp AS (SELECT * FROM campaigns WHERE media = ANY(%(media)s))
"""

ALERT_SQL = "WITH " + BASELINE_CTE + """
SELECT camp.campaign_id, 'ctr_drop' AS rule, cur.ctr AS value, base.ctr AS baseline, cur.ctr / base.ctr AS ratio
FROM camp JOIN cur USING (campaign_id) JOIN base USING (campaign_id)
WHERE base.days >= %(min_days)s AND base.ctr > 0 AND cur.ctr < base.ctr * %(ctr_ratio)s

UNION ALL
SELECT camp.campaign_id, 'overspend', cur.spend, camp.daily_budget, cur.spend / camp.daily_budget
FROM camp JOIN cur USING (campaign_id)
WHERE camp.daily_budget > 0 AND cur.spend > camp.daily_budget * %(overspend_ratio)s

UNION ALL
-- 전환 0건(CPA 계산 불가)인데 비용이 나갔다면 그것도 CPA 급등으로 본다
SELECT camp.campaign_id, 'cpa_spike', cur.cpa, base.cpa, cur.cpa / base.cpa
FROM camp JOIN cur USING (campaign_id) JOIN base USING (campaign_id)
WHERE base.days >= %(min_days)s AND base.cpa > 0
  AND (cur.cpa > base.cpa * %(cpa_ratio)s OR (cur.conversions = 0 AND cur.spend > 0))

UNION ALL
SELECT camp.campaign_id, 'missing_data', NULL, NULL, NULL
FROM camp
WHERE camp.is_active
  AND NOT EXISTS (SELECT 1 FROM cur WHERE cur.campaign_id = camp.campaign_id)
"""

CAMPAIGN_SQL = "WITH " + BASELINE_CTE + """
SELECT camp.campaign_id, camp.media, camp.name, camp.daily_budget,
       cur.spend, cur.conversions, cur.ctr, cur.cpa,
       RANK() OVER (ORDER BY cur.conversions DESC NULLS LAST) AS conv_rank
FROM camp JOIN cur USING (campaign_id)
ORDER BY conv_rank, camp.campaign_id
"""

WEEKDAYS = "월화수목금토일"
SEVERITY_ORDER = {"critical": 0, "warn": 1, "info": 2}


# ── 표기 (LLM이 그대로 옮겨 쓸 문자열) ──────────────────────────────
def won(x) -> str | None:
    return None if x is None else f"{int(round(Decimal(x))):,}원"


def count(x) -> str | None:
    return None if x is None else f"{int(x):,}건"


def change(x) -> str | None:
    return None if x is None else f"{float(x):+.1f}%"


def pct(x, digits: int = 1) -> str | None:
    return None if x is None else f"{float(x):.{digits}f}%"


def ctr_pct(x) -> str | None:
    return None if x is None else f"{float(x) * 100:.2f}%"


def date_label(d: dt.date) -> str:
    return f"{d.month}/{d.day}({WEEKDAYS[d.weekday()]})"


def _agg_block(r: dict) -> dict:
    return {
        "spend": won(r["spend"]),
        "spend_change": change(r["spend_chg"]),
        "impressions_change": change(r["impressions_chg"]),
        "clicks_change": change(r["clicks_chg"]),
        "ctr": ctr_pct(r["ctr"]),
        "ctr_change": change(r["ctr_chg"]),
        "conversions": count(r["conversions"]),
        "conversions_change": change(r["conversions_chg"]),
        "cpa": won(r["cpa"]),
        "cpa_change": change(r["cpa_chg"]),
    }


def _describe(a: dict, names: dict) -> str:
    name, media = names[a["campaign_id"]]
    who = f"{name}({MEDIA_LABELS[media]})"
    rule = a["rule"]
    if rule == "overspend":
        return f"{who} 예산 대비 {pct(a['ratio'] * 100, 0)} 소진 ({won(a['value'])} / 일예산 {won(a['baseline'])})"
    if rule == "ctr_drop":
        return f"{who} CTR {ctr_pct(a['value'])} → 7일 평균 {ctr_pct(a['baseline'])} 대비 급락"
    if rule == "cpa_spike":
        if a["value"] is None:
            return f"{who} 비용은 나갔으나 전환 0건 (7일 평균 CPA {won(a['baseline'])})"
        return f"{who} CPA {won(a['value'])} → 7일 평균 {won(a['baseline'])} 대비 {pct(a['ratio'] * 100, 0)}"
    if rule == "missing_data":
        return f"{who} 활성 캠페인인데 전일 데이터가 들어오지 않음"
    return f"{who} {rule}"


# ── 메인 ──────────────────────────────────────────────────────────
def build_report(
    conn: psycopg.Connection,
    report_date: dt.date,
    cfg: dict,
    included_media: list[str],
    failed_media: list[str] | None = None,
) -> tuple[dict, list[dict]]:
    """(LLM에 줄 리포트 JSON, alerts 테이블에 넣을 행들) 을 반환."""
    acfg = cfg["alerts"]
    params = {
        "d": report_date,
        "n": acfg["baseline_days"],
        "media": included_media,
        "min_days": acfg["min_baseline_days"],
        "ctr_ratio": acfg["ctr_drop"]["ratio"],
        "overspend_ratio": acfg["overspend"]["ratio"],
        "cpa_ratio": acfg["cpa_spike"]["ratio"],
    }
    with conn.cursor() as cur:
        cur.execute(MEDIA_SQL, params)
        media_rows = cur.fetchall()
        cur.execute(ALERT_SQL, params)
        alert_rows = cur.fetchall()
        cur.execute(CAMPAIGN_SQL, params)
        camp_rows = cur.fetchall()
        cur.execute("SELECT campaign_id, name, media FROM campaigns")
        names = {r["campaign_id"]: (r["name"], r["media"]) for r in cur.fetchall()}

    total = next((r for r in media_rows if r["media"] is None), None)
    by_media = []
    for r in media_rows:
        if r["media"] is None or r["spend"] is None:
            continue
        block = {"media": MEDIA_LABELS[r["media"]], "spend_share": pct(r["spend_share"], 0)}
        block.update(_agg_block(r))
        by_media.append(block)

    for a in alert_rows:
        a["severity"] = acfg[a["rule"]]["severity"]
    alert_rows.sort(key=lambda a: (SEVERITY_ORDER.get(a["severity"], 9), a["rule"], a["campaign_id"]))

    alerts_json = [
        {
            "severity": a["severity"],
            "rule": a["rule"],
            "campaign": names[a["campaign_id"]][0],
            "media": MEDIA_LABELS[names[a["campaign_id"]][1]],
            "description": _describe(a, names),
        }
        for a in alert_rows
    ]

    top = [
        {
            "campaign": r["name"],
            "media": MEDIA_LABELS[r["media"]],
            "conversions": count(r["conversions"]),
            "cpa": won(r["cpa"]),
            "ctr": ctr_pct(r["ctr"]),
        }
        for r in camp_rows
        if r["conv_rank"] <= 3
    ]

    data_issues = [f"{MEDIA_LABELS.get(m, m)} 데이터 수집 실패 → 합계·탐지에서 제외" for m in (failed_media or [])]

    report = {
        "report_date": report_date.isoformat(),
        "report_date_label": date_label(report_date),
        "total": _agg_block(total) if total and total["spend"] is not None else None,
        "by_media": by_media,
        "top_campaigns_by_conversions": top,
        "alerts": alerts_json,
        "alert_count": count(len(alerts_json)),
        "data_issues": data_issues,
    }
    return report, [{**a, "report_date": report_date} for a in alert_rows]


def save_alerts(conn: psycopg.Connection, report_date: dt.date, alert_rows: list[dict]) -> None:
    """같은 날짜 재실행 시 중복이 생기지 않도록 그 날짜분을 지우고 다시 쓴다."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM alerts WHERE report_date = %s", (report_date,))
        cur.executemany(
            "INSERT INTO alerts (report_date, campaign_id, rule, value, baseline, severity) "
            "VALUES (%(report_date)s, %(campaign_id)s, %(rule)s, %(value)s, %(baseline)s, %(severity)s)",
            alert_rows,
        )
    conn.commit()
