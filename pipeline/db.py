"""DB 연결.

- DATABASE_URL 이 있으면 그 PostgreSQL(Supabase 등)에 연결한다.
- 없으면 로컬 개발용 내장 PostgreSQL(pgserver, .pgdata/)을 띄워 쓴다.
"""
from __future__ import annotations

import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from pipeline.config import ROOT

_local_server = None


def database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        return url
    return _local_url()


def _local_url() -> str:
    global _local_server
    if _local_server is None:
        try:
            import pgserver
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "DATABASE_URL 이 비어 있습니다. .env 에 Supabase 주소를 넣거나 "
                "`pip install -r requirements-dev.txt` 로 로컬 DB(pgserver)를 설치하세요."
            ) from e
        pgdata = Path(os.getenv("LOCAL_PGDATA", ROOT / ".pgdata"))
        # cleanup_mode=None: 프로세스가 끝나도 서버를 내리지 않아 다음 실행이 빠름
        _local_server = pgserver.get_server(pgdata, cleanup_mode=None)
    return _local_server.get_uri()


def connect() -> psycopg.Connection:
    # prepare_threshold=None: Supabase 커넥션 풀러(pgbouncer)와 호환되도록 prepared statement 비활성화
    return psycopg.connect(database_url(), row_factory=dict_row, prepare_threshold=None)


def init_schema(conn: psycopg.Connection) -> None:
    sql = (ROOT / "sql" / "schema.sql").read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
