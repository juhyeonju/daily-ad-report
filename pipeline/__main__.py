"""실행 진입점.

  python -m pipeline init-db                 # 테이블 생성 (여러 번 실행해도 안전)
  python -m pipeline backfill --days 30      # 최근 30일 데이터 초기 적재
  python -m pipeline daily                   # 어제(KST) 리포트 생성 → 발송
  python -m pipeline daily --date 2026-11-15 --dry-run   # 특정 날짜, 발송 없이 출력만
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
import uuid

from dotenv import load_dotenv

from pipeline import db, loader, metrics, notifier, summarizer
from pipeline.collector import build_adapters, collect
from pipeline.config import ROOT, load_config, yesterday_kst

log = logging.getLogger("pipeline")


def _ingest(conn, adapters, d: dt.date) -> dict:
    results = collect(adapters, d)
    loaded = loader.load(conn, d, results)
    return {
        "loaded": loaded,
        "failed": {r.media: r.error for r in results if not r.ok},
    }


def cmd_init_db(args, cfg) -> int:
    with db.connect() as conn:
        db.init_schema(conn)
    print("스키마 준비 완료")
    return 0


def cmd_backfill(args, cfg) -> int:
    end = args.end or yesterday_kst()
    adapters = build_adapters(cfg)
    failed_days = 0
    with db.connect() as conn:
        db.init_schema(conn)
        for i in range(args.days - 1, -1, -1):
            d = end - dt.timedelta(days=i)
            res = _ingest(conn, adapters, d)
            rows = sum(res["loaded"].values())
            status = "OK" if not res["failed"] else f"일부 실패 {list(res['failed'])}"
            failed_days += bool(res["failed"])
            print(f"{d}  {rows:>3}행  {status}")
    return 1 if failed_days else 0


def cmd_daily(args, cfg) -> int:
    report_date: dt.date = args.date or yesterday_kst()
    run_id = uuid.uuid4()
    started = dt.datetime.now(dt.timezone.utc)
    detail: dict = {"steps": {}}
    status = "failed"
    conn = None
    try:
        conn = db.connect()
        db.init_schema(conn)
        adapters = build_adapters(cfg)

        # 1) 최근 N일 중 비어 있는 날짜 자동 보충 (첫 실행·누락 실행 대비)
        n = cfg.get("catch_up_days", 7)
        have = loader.dates_with_data(conn, report_date - dt.timedelta(days=n), report_date - dt.timedelta(days=1))
        gaps = [report_date - dt.timedelta(days=i) for i in range(n, 0, -1)]
        gaps = [d for d in gaps if d not in have]
        for d in gaps:
            _ingest(conn, adapters, d)
        detail["steps"]["catch_up"] = [d.isoformat() for d in gaps]

        # 2) 수집·적재
        ing = _ingest(conn, adapters, report_date)
        detail["steps"]["collect"] = ing
        failed_media = sorted(ing["failed"])
        included = [a.media for a in adapters if a.media not in failed_media]
        if not included:
            raise RuntimeError(f"모든 매체 수집 실패: {ing['failed']}")

        # 3) 지표·이상 탐지 (SQL)
        report, alert_rows = metrics.build_report(conn, report_date, cfg, included, failed_media)
        metrics.save_alerts(conn, report_date, alert_rows)
        detail["steps"]["alerts"] = [f"{a['rule']}:{a['campaign_id']}" for a in alert_rows]

        # 4) 요약 + 검증
        composed = summarizer.compose(report, cfg, use_llm=not args.no_llm)
        message = summarizer.render_message(report, composed)
        detail["steps"]["summary"] = {
            "source": composed.source, "attempts": composed.attempts,
            "validation": composed.validation, "reason": composed.reason,
        }

        out = ROOT / "out"
        out.mkdir(exist_ok=True)
        (out / f"report_{report_date}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
        (out / f"message_{report_date}.txt").write_text(message, "utf-8")

        # 5) 발송
        sent = notifier.notify(message, cfg, subject=f"[광고 리포트] {report['report_date_label']}", dry_run=args.dry_run)
        detail["steps"]["notify"] = sent

        degraded = bool(failed_media) or sent["channel"] in ("email", "console")
        status = "partial" if degraded else "success"
        return 0
    except Exception as e:
        log.exception("daily run failed")
        detail["error"] = f"{type(e).__name__}: {e}"
        if not args.dry_run:
            try:
                notifier.notify(
                    f"🚨 {report_date} 광고 리포트 생성 실패\n{detail['error']}\nrun_id: {run_id}",
                    cfg, subject=f"[광고 리포트 실패] {report_date}",
                )
            except Exception:
                log.exception("failure notification failed")
        return 1
    finally:
        if conn is not None:
            try:
                conn.rollback()
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO run_logs (run_id, report_date, status, detail, started_at, finished_at) "
                        "VALUES (%s, %s, %s, %s, %s, now())",
                        (run_id, report_date, status, json.dumps(detail, ensure_ascii=False, default=str), started),
                    )
                conn.commit()
            finally:
                conn.close()
        print(f"[run {run_id}] {report_date} → {status}", file=sys.stderr)


def main(argv=None) -> int:
    load_dotenv(ROOT / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    p = argparse.ArgumentParser(prog="python -m pipeline", description="광고 성과 일일 리포트")
    p.add_argument("--config", help="설정 파일 경로 (기본: config.yaml)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db", help="테이블 생성")

    b = sub.add_parser("backfill", help="최근 N일 데이터 적재")
    b.add_argument("--days", type=int, default=30)
    b.add_argument("--end", type=dt.date.fromisoformat, help="마지막 날짜 (기본: 어제)")

    d = sub.add_parser("daily", help="일일 리포트 실행")
    d.add_argument("--date", type=dt.date.fromisoformat, help="리포트 날짜 (기본: 어제 KST)")
    d.add_argument("--dry-run", action="store_true", help="발송하지 않고 화면에만 출력")
    d.add_argument("--no-llm", action="store_true", help="LLM 없이 템플릿 리포트")

    args = p.parse_args(argv)
    cfg = load_config(args.config)
    return {"init-db": cmd_init_db, "backfill": cmd_backfill, "daily": cmd_daily}[args.cmd](args, cfg)


if __name__ == "__main__":
    sys.exit(main())
