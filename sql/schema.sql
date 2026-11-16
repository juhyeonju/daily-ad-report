-- ─────────────────────────────────────────────
-- Daily Ad Report — PostgreSQL 스키마 (Supabase 호환)
-- 여러 번 실행해도 안전하도록 IF NOT EXISTS / OR REPLACE 사용
-- ─────────────────────────────────────────────

-- 캠페인 마스터
CREATE TABLE IF NOT EXISTS campaigns (
  campaign_id   TEXT PRIMARY KEY,                 -- 매체 접두어 포함: meta_23851001
  media         TEXT NOT NULL CHECK (media IN ('meta', 'google', 'tiktok')),
  name          TEXT NOT NULL,
  advertiser    TEXT NOT NULL,                    -- 가상 광고주명
  daily_budget  NUMERIC(14,2),                    -- KRW
  is_active     BOOLEAN NOT NULL DEFAULT true,    -- missing_data 판정용
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 원본 보관 (재처리·디버깅용). 실행마다 쌓이는 로그 성격의 테이블.
CREATE TABLE IF NOT EXISTS raw_responses (
  id           BIGSERIAL PRIMARY KEY,
  media        TEXT NOT NULL,
  report_date  DATE NOT NULL,
  payload      JSONB NOT NULL,
  fetched_at   TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS raw_responses_media_date_idx ON raw_responses (media, report_date);

-- 정규화된 일별 성과 (KRW, KST 기준)
CREATE TABLE IF NOT EXISTS fact_daily (
  report_date  DATE NOT NULL,
  campaign_id  TEXT NOT NULL REFERENCES campaigns,
  spend        NUMERIC(14,2) NOT NULL CHECK (spend >= 0),
  impressions  BIGINT NOT NULL CHECK (impressions >= 0),
  clicks       BIGINT NOT NULL CHECK (clicks >= 0),
  conversions  BIGINT NOT NULL CHECK (conversions >= 0),
  loaded_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (report_date, campaign_id)
);

-- 이상 탐지 결과 (같은 날짜 재실행 시 해당 날짜분을 지우고 다시 씀)
CREATE TABLE IF NOT EXISTS alerts (
  id           BIGSERIAL PRIMARY KEY,
  report_date  DATE NOT NULL,
  campaign_id  TEXT REFERENCES campaigns,
  rule         TEXT NOT NULL,           -- ctr_drop | overspend | cpa_spike | missing_data
  value        NUMERIC,
  baseline     NUMERIC,
  severity     TEXT,                    -- info | warn | critical
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS alerts_date_idx ON alerts (report_date);

-- 실행 이력
CREATE TABLE IF NOT EXISTS run_logs (
  run_id       UUID PRIMARY KEY,
  report_date  DATE,
  status       TEXT,                    -- success | partial | failed
  detail       JSONB,
  started_at   TIMESTAMPTZ,
  finished_at  TIMESTAMPTZ
);

-- 지표 계산 View
CREATE OR REPLACE VIEW v_daily_kpi AS
SELECT report_date, campaign_id, spend, impressions, clicks, conversions,
       clicks::numeric / NULLIF(impressions, 0)  AS ctr,
       spend / NULLIF(clicks, 0)                 AS cpc,
       spend / NULLIF(conversions, 0)            AS cpa
FROM fact_daily;

-- 보안: RLS를 켜고 정책을 만들지 않는다.
-- → Supabase의 anon / authenticated 키로는 아무 행도 읽고 쓸 수 없고,
--   파이프라인(GitHub Actions)이 쓰는 DB 소유자 연결만 접근 가능.
ALTER TABLE campaigns     ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw_responses ENABLE ROW LEVEL SECURITY;
ALTER TABLE fact_daily    ENABLE ROW LEVEL SECURITY;
ALTER TABLE alerts        ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_logs      ENABLE ROW LEVEL SECURITY;
-- View도 호출자 권한으로 실행되게 해서 RLS를 우회하지 못하게 함 (PG15+)
ALTER VIEW v_daily_kpi SET (security_invoker = true);
