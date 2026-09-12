#!/usr/bin/env python3
"""Optional Postgres-backed queue helpers for PPT Master generation tasks.

The JSON job runner remains the zero-dependency fallback. This module is used
when DATABASE_URL is configured and psycopg is installed.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).with_name("postgres_schema.sql")


class PostgresQueueUnavailable(RuntimeError):
    pass


def _import_psycopg():
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise PostgresQueueUnavailable(
            "psycopg is not installed. Install with: pip install 'psycopg[binary]>=3.1'"
        ) from exc
    return psycopg, dict_row


def _database_url(database_url: str | None = None) -> str:
    value = database_url or os.environ.get("DATABASE_URL", "")
    if not value:
        raise PostgresQueueUnavailable("DATABASE_URL is not set")
    return value


@dataclass
class ClaimedTask:
    id: str
    brief_path: str | None
    source_path: str | None
    project_dir: str | None
    output_path: str | None
    max_attempts: int
    brief_json: dict[str, Any] | None = None
    source_text: str | None = None


def connect(database_url: str | None = None):
    psycopg, dict_row = _import_psycopg()
    return psycopg.connect(_database_url(database_url), row_factory=dict_row)


def apply_schema(database_url: str | None = None) -> None:
    with connect(database_url) as conn:
        conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()


def enqueue_task(
    *,
    brief_path: str | None = None,
    source_path: str | None = None,
    brief_json: dict[str, Any] | None = None,
    source_text: str | None = None,
    job_id: str | None = None,
    priority: int = 100,
    max_attempts: int = 2,
    owner_user_id: str = "",
    quota_debited: bool = False,
    project_dir: str | None = None,
    output_path: str | None = None,
    database_url: str | None = None,
) -> dict[str, Any]:
    if not (brief_path or brief_json):
        raise ValueError("brief_path or brief_json is required")
    if not (source_path or source_text):
        raise ValueError("source_path or source_text is required")

    with connect(database_url) as conn:
        row = conn.execute(
            """
            INSERT INTO generation_tasks (
              id, priority, max_attempts, owner_user_id, quota_debited, brief_path, source_path, brief_json,
              source_text, project_dir, output_path
            )
            VALUES (
              COALESCE(%(id)s, 'job_' || encode(gen_random_bytes(6), 'hex')),
              %(priority)s, %(max_attempts)s, %(owner_user_id)s, %(quota_debited)s, %(brief_path)s, %(source_path)s,
              %(brief_json)s::jsonb, %(source_text)s, %(project_dir)s, %(output_path)s
            )
            RETURNING *
            """,
            {
                "id": job_id,
                "priority": priority,
                "max_attempts": max_attempts,
                "owner_user_id": owner_user_id or "",
                "quota_debited": bool(quota_debited),
                "brief_path": brief_path,
                "source_path": source_path,
                "brief_json": json.dumps(brief_json, ensure_ascii=False) if brief_json is not None else None,
                "source_text": source_text,
                "project_dir": project_dir,
                "output_path": output_path,
            },
        ).fetchone()
        conn.execute(
            """
            INSERT INTO task_events (task_id, event_type, message)
            VALUES (%s, 'queued', 'Task queued')
            """,
            (row["id"],),
        )
        conn.commit()
        return dict(row)


def claim_next_task(*, worker_id: str | None = None, database_url: str | None = None) -> ClaimedTask | None:
    worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
    with connect(database_url) as conn:
        row = conn.execute(
            """
            WITH next_task AS (
              SELECT id
              FROM generation_tasks
              WHERE status = 'queued'
              ORDER BY priority ASC, created_at ASC
              FOR UPDATE SKIP LOCKED
              LIMIT 1
            )
            UPDATE generation_tasks task
            SET status = 'running',
                attempts = attempts + 1,
                locked_by = %(worker_id)s,
                locked_at = now(),
                started_at = COALESCE(started_at, now())
            FROM next_task
            WHERE task.id = next_task.id
            RETURNING task.*
            """,
            {"worker_id": worker_id},
        ).fetchone()
        if not row:
            conn.commit()
            return None
        conn.execute(
            """
            INSERT INTO task_events (task_id, event_type, message, metadata_json)
            VALUES (%s, 'running', 'Task claimed by worker', %s::jsonb)
            """,
            (row["id"], json.dumps({"worker_id": worker_id})),
        )
        conn.commit()
        return ClaimedTask(
            id=row["id"],
            brief_path=row.get("brief_path"),
            source_path=row.get("source_path"),
            project_dir=row.get("project_dir"),
            output_path=row.get("output_path"),
            max_attempts=row.get("max_attempts") or 2,
            brief_json=row.get("brief_json"),
            source_text=row.get("source_text"),
        )


def update_task_status(
    task_id: str,
    status: str,
    *,
    message: str,
    metadata: dict[str, Any] | None = None,
    output_path: str | None = None,
    generation_report_path: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    database_url: str | None = None,
) -> None:
    metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
    with connect(database_url) as conn:
        result = conn.execute(
            """
            UPDATE generation_tasks
            SET status = %(status)s::generation_task_status,
                output_path = COALESCE(%(output_path)s, output_path),
                generation_report_path = COALESCE(%(generation_report_path)s, generation_report_path),
                error_code = %(error_code)s,
                error_message = %(error_message)s,
                completed_at = CASE
                  WHEN %(status)s::generation_task_status IN ('completed', 'failed', 'cancelled') THEN now()
                  ELSE completed_at
                END
            WHERE id = %(task_id)s AND status <> 'cancelled'
            """,
            {
                "task_id": task_id,
                "status": status,
                "output_path": output_path,
                "generation_report_path": generation_report_path,
                "error_code": error_code,
                "error_message": error_message,
            },
        )
        if getattr(result, "rowcount", 1) == 0:
            conn.commit()
            return
        conn.execute(
            """
            INSERT INTO task_events (task_id, event_type, message, metadata_json)
            VALUES (%s, %s, %s, %s::jsonb)
            """,
            (task_id, status, message, metadata_json),
        )
        conn.commit()


def get_task(task_id: str, *, database_url: str | None = None) -> dict[str, Any] | None:
    with connect(database_url) as conn:
        row = conn.execute("SELECT * FROM generation_tasks WHERE id = %s", (task_id,)).fetchone()
        return dict(row) if row else None


def is_task_cancelled(task_id: str, *, database_url: str | None = None) -> bool:
    with connect(database_url) as conn:
        row = conn.execute("SELECT status FROM generation_tasks WHERE id = %s", (task_id,)).fetchone()
        return bool(row and str(row.get("status") or "") == "cancelled")


def list_tasks(*, limit: int = 50, owner_user_id: str = "", database_url: str | None = None) -> list[dict[str, Any]]:
    limit = max(1, min(200, int(limit or 50)))
    with connect(database_url) as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM generation_tasks
            WHERE (%s = '' OR owner_user_id = %s)
            ORDER BY updated_at DESC, created_at DESC
            LIMIT %s
            """,
            (owner_user_id or "", owner_user_id or "", limit),
        ).fetchall()
        return [dict(row) for row in rows]


def list_events(task_id: str, *, database_url: str | None = None) -> list[dict[str, Any]]:
    with connect(database_url) as conn:
        rows = conn.execute(
            "SELECT * FROM task_events WHERE task_id = %s ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def cancel_task(task_id: str, *, database_url: str | None = None) -> dict[str, Any] | None:
    """Cancel a queued or running Postgres task and release its worker lock."""
    with connect(database_url) as conn:
        row = conn.execute(
            """
            UPDATE generation_tasks
            SET status = 'cancelled'::generation_task_status,
                error_code = 'cancelled',
                error_message = 'Job cancelled by user',
                locked_by = NULL,
                locked_at = NULL,
                completed_at = now()
            WHERE id = %s AND status NOT IN ('completed', 'failed', 'cancelled')
            RETURNING *
            """,
            (task_id,),
        ).fetchone()
        if not row:
            current = conn.execute("SELECT * FROM generation_tasks WHERE id = %s", (task_id,)).fetchone()
            conn.commit()
            return dict(current) if current and str(current.get("status")) == "cancelled" else None
        conn.execute(
            """
            INSERT INTO task_events (task_id, event_type, message, metadata_json)
            VALUES (%s, %s, %s, %s::jsonb)
            """,
            (task_id, "cancelled", "Job cancelled by user", json.dumps({}, ensure_ascii=False)),
        )
        conn.commit()
        return dict(row)


def retry_task(
    task_id: str,
    *,
    max_attempts: int | None = None,
    event_message: str = "Task queued for retry",
    event_metadata: dict[str, Any] | None = None,
    database_url: str | None = None,
) -> dict[str, Any] | None:
    with connect(database_url) as conn:
        current = conn.execute(
            """
            SELECT *
            FROM generation_tasks
            WHERE id = %(task_id)s
            FOR UPDATE
            """,
            {"task_id": task_id},
        ).fetchone()
        if not current:
            conn.commit()
            return None

        current = dict(current)
        status = str(current.get("status") or "")
        if status not in {"failed", "cancelled"}:
            raise ValueError("Only failed or cancelled tasks can be retried")

        next_attempts = max(1, int(max_attempts or current.get("max_attempts") or 2))
        row = conn.execute(
            """
            UPDATE generation_tasks
            SET status = %(status)s::generation_task_status,
                attempts = %(attempts)s,
                max_attempts = %(max_attempts)s,
                output_path = %(output_path)s,
                generation_report_path = %(generation_report_path)s,
                error_code = %(error_code)s,
                error_message = %(error_message)s,
                locked_by = NULL,
                locked_at = NULL,
                started_at = NULL,
                completed_at = NULL
            WHERE id = %(task_id)s
            RETURNING *
            """,
            {
                "task_id": task_id,
                "status": "queued",
                "attempts": 0,
                "max_attempts": next_attempts,
                "output_path": None,
                "generation_report_path": None,
                "error_code": None,
                "error_message": None,
            },
        ).fetchone()
        metadata = {
            "previous_status": status,
            "previous_error_code": current.get("error_code"),
            "previous_error_message": current.get("error_message"),
        }
        if event_metadata:
            metadata.update(event_metadata)
        conn.execute(
            """
            INSERT INTO task_events (task_id, event_type, message, metadata_json)
            VALUES (%s, %s, %s, %s::jsonb)
            """,
            (task_id, "queued", event_message, json.dumps(metadata, ensure_ascii=False)),
        )
        conn.commit()
        return dict(row) if row else None


def main() -> None:
    parser = argparse.ArgumentParser(description="PPT Master Postgres queue helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Apply Postgres schema")

    enqueue = subparsers.add_parser("enqueue", help="Enqueue one task")
    enqueue.add_argument("--brief-path")
    enqueue.add_argument("--source-path")
    enqueue.add_argument("--job-id")
    enqueue.add_argument("--project-dir")
    enqueue.add_argument("--output-path")
    enqueue.add_argument("--max-attempts", type=int, default=2)

    claim = subparsers.add_parser("claim", help="Claim the next queued task")
    claim.add_argument("--worker-id")

    args = parser.parse_args()

    try:
        if args.command == "init":
            apply_schema()
            print(json.dumps({"status": "ok", "schema": str(SCHEMA_PATH)}, indent=2))
            return

        if args.command == "enqueue":
            row = enqueue_task(
                brief_path=args.brief_path,
                source_path=args.source_path,
                job_id=args.job_id,
                project_dir=args.project_dir,
                output_path=args.output_path,
                max_attempts=args.max_attempts,
            )
            print(json.dumps({"id": row["id"], "status": row["status"]}, ensure_ascii=False, indent=2, default=str))
            return

        if args.command == "claim":
            task = claim_next_task(worker_id=args.worker_id)
            print(json.dumps(task.__dict__ if task else None, ensure_ascii=False, indent=2, default=str))
    except Exception as exc:
        print(json.dumps({
            "status": "error",
            "error_code": exc.__class__.__name__,
            "error_message": str(exc),
        }, ensure_ascii=False, indent=2))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
