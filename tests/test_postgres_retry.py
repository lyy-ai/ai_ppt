import json
import unittest
from unittest.mock import patch

from cloud_generator import postgres_queue


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._row if isinstance(self._row, list) else []


class _CallPattern:
    def __init__(self, sql_substring, row=None, rows=None):
        self.sql_substring = sql_substring
        self.row = row
        self.rows = rows
        self.matched = False


class _MultiStepFakeConn:
    def __init__(self, patterns):
        self.patterns = list(patterns)
        self.calls = []
        self.committed = False
        self._cursor = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        normalized = " ".join(sql.split())
        for pattern in self.patterns:
            if pattern.sql_substring in normalized and not pattern.matched:
                pattern.matched = True
                if pattern.rows is not None:
                    return _FakeResult(pattern.rows)
                return _FakeResult(pattern.row)
        return _FakeResult(None)

    def commit(self):
        self.committed = True


class RetryTaskUnitTests(unittest.TestCase):
    def test_cancel_task_marks_active_task_cancelled_and_records_event(self):
        conn = _MultiStepFakeConn([
            _CallPattern("UPDATE generation_tasks", row={
                "id": "task_cancel",
                "status": "cancelled",
                "error_code": "cancelled",
                "error_message": "Job cancelled by user",
            }),
            _CallPattern("INSERT INTO task_events", row=None),
        ])

        with patch.object(postgres_queue, "connect", return_value=conn):
            row = postgres_queue.cancel_task("task_cancel")

        self.assertEqual(row["status"], "cancelled")
        self.assertTrue(conn.committed)
        update_sql, update_params = conn.calls[0]
        self.assertIn("status = 'cancelled'", update_sql)
        self.assertEqual(update_params, ("task_cancel",))
        event_sql, event_params = conn.calls[1]
        self.assertIn("INSERT INTO task_events", event_sql)
        self.assertEqual(event_params[0:3], ("task_cancel", "cancelled", "Job cancelled by user"))

    def test_retry_task_requeues_failed_task_and_clears_failure_fields(self):
        selected_row = {
            "id": "task_123",
            "status": "failed",
            "max_attempts": 2,
            "attempts": 1,
            "error_code": "Boom",
            "error_message": "forced failure",
        }
        updated_row = {
            "id": "task_123",
            "status": "queued",
            "max_attempts": 5,
            "attempts": 0,
            "error_code": None,
            "error_message": None,
            "output_path": None,
            "generation_report_path": None,
        }
        conn = _MultiStepFakeConn([
            _CallPattern("FROM generation_tasks", row=selected_row),
            _CallPattern("UPDATE generation_tasks", row=updated_row),
            _CallPattern("INSERT INTO task_events", row=None),
        ])

        with patch.object(postgres_queue, "connect", return_value=conn):
            row = postgres_queue.retry_task(
                "task_123",
                max_attempts=5,
                event_message="Task queued for retry",
                event_metadata={"trigger": "api"},
            )

        self.assertEqual(row["status"], "queued")
        self.assertEqual(row["max_attempts"], 5)
        self.assertTrue(conn.committed)

        update_sql, update_params = conn.calls[1]
        self.assertIn("UPDATE generation_tasks", update_sql)
        self.assertEqual(update_params["task_id"], "task_123")
        self.assertEqual(update_params["status"], "queued")
        self.assertEqual(update_params["max_attempts"], 5)
        self.assertEqual(update_params["attempts"], 0)
        self.assertIsNone(update_params["error_code"])
        self.assertIsNone(update_params["error_message"])
        self.assertIsNone(update_params["output_path"])
        self.assertIsNone(update_params["generation_report_path"])

        event_sql, event_params = conn.calls[2]
        self.assertIn("INSERT INTO task_events", event_sql)
        self.assertEqual(event_params[0], "task_123")
        self.assertEqual(event_params[1], "queued")
        self.assertEqual(event_params[2], "Task queued for retry")
        self.assertIn('"previous_status": "failed"', event_params[3])
        self.assertIn('"trigger": "api"', event_params[3])

    def test_retry_task_rejects_non_terminal_status(self):
        conn = _MultiStepFakeConn([
            _CallPattern("FROM generation_tasks", row={
                "id": "task_running",
                "status": "running",
                "max_attempts": 2,
            }),
        ])

        with patch.object(postgres_queue, "connect", return_value=conn):
            with self.assertRaisesRegex(ValueError, "Only failed or cancelled tasks can be retried"):
                postgres_queue.retry_task("task_running")

    def test_retry_task_allows_cancelled_status(self):
        selected_row = {
            "id": "task_cancelled",
            "status": "cancelled",
            "max_attempts": 3,
            "attempts": 1,
            "error_code": "CANCELLED_BY_USER",
            "error_message": "User cancelled",
        }
        updated_row = {
            "id": "task_cancelled",
            "status": "queued",
            "max_attempts": 5,
            "attempts": 0,
            "error_code": None,
            "error_message": None,
            "output_path": None,
            "generation_report_path": None,
        }
        conn = _MultiStepFakeConn([
            _CallPattern("FROM generation_tasks", row=selected_row),
            _CallPattern("UPDATE generation_tasks", row=updated_row),
            _CallPattern("INSERT INTO task_events", row=None),
        ])

        with patch.object(postgres_queue, "connect", return_value=conn):
            row = postgres_queue.retry_task("task_cancelled", max_attempts=5)

        self.assertEqual(row["status"], "queued")
        self.assertEqual(row["error_code"], None)
        self.assertTrue(conn.committed)

    def test_retry_task_clears_locked_by_and_timestamps(self):
        selected_row = {
            "id": "task_locked",
            "status": "failed",
            "max_attempts": 2,
            "attempts": 1,
            "error_code": "Timeout",
            "error_message": "worker timeout",
            "locked_by": "worker-1",
            "started_at": "2026-01-01T00:00:00Z",
            "completed_at": "2026-01-01T00:05:00Z",
        }
        updated_row = {
            "id": "task_locked",
            "status": "queued",
            "max_attempts": 3,
            "attempts": 0,
            "error_code": None,
            "error_message": None,
            "locked_by": None,
            "started_at": None,
            "completed_at": None,
            "output_path": None,
            "generation_report_path": None,
        }
        conn = _MultiStepFakeConn([
            _CallPattern("FROM generation_tasks", row=selected_row),
            _CallPattern("UPDATE generation_tasks", row=updated_row),
            _CallPattern("INSERT INTO task_events", row=None),
        ])

        with patch.object(postgres_queue, "connect", return_value=conn):
            row = postgres_queue.retry_task("task_locked", max_attempts=3)

        self.assertEqual(row["status"], "queued")
        self.assertIsNone(row.get("locked_by"))
        self.assertTrue(conn.committed)

        update_sql, update_params = conn.calls[1]
        self.assertIn("locked_by = NULL", update_sql)
        self.assertIn("locked_at = NULL", update_sql)
        self.assertIn("started_at = NULL", update_sql)
        self.assertIn("completed_at = NULL", update_sql)


class RetryTaskIntegrationTests(unittest.TestCase):
    """End-to-end flow: enqueue → claim → fail → retry → re-claim."""

    def _make_task_row(self, id, status="queued", max_attempts=2, attempts=0,
                       error_code=None, error_message=None,
                       owner_user_id="", quota_debited=False,
                       brief_path=None, source_path=None,
                       project_dir=None, output_path=None,
                       locked_by=None):
        return {
            "id": id,
            "status": status,
            "priority": 100,
            "attempts": attempts,
            "max_attempts": max_attempts,
            "owner_user_id": owner_user_id,
            "quota_debited": quota_debited,
            "brief_json": {"page_count": 4},
            "brief_path": brief_path,
            "source_text": "source content",
            "source_path": source_path,
            "project_dir": project_dir,
            "output_path": output_path,
            "generation_report_path": None,
            "error_code": error_code,
            "error_message": error_message,
            "locked_by": locked_by,
            "locked_at": None,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "started_at": None,
            "completed_at": None,
        }

    def test_full_enqueue_claim_retry_reclaim_flow(self):
        task_id = "pg_flow_" + str(id(self))

        enqueued_row = self._make_task_row(task_id, status="queued", max_attempts=2, attempts=0)
        claimed_row = self._make_task_row(task_id, status="running", max_attempts=2, attempts=1, locked_by="w1")
        failed_row = self._make_task_row(task_id, status="failed", max_attempts=2, attempts=1,
                                         error_code="Boom", error_message="forced")
        retried_row = self._make_task_row(task_id, status="queued", max_attempts=5, attempts=0)
        reclaimed_row = self._make_task_row(task_id, status="running", max_attempts=5, attempts=1, locked_by="w1")

        conn = _MultiStepFakeConn([
            _CallPattern("INSERT INTO generation_tasks", row=enqueued_row),
            _CallPattern("INSERT INTO task_events", row=None),
            _CallPattern("WITH next_task AS", row=claimed_row),
            _CallPattern("INSERT INTO task_events", row=None),
            _CallPattern("FROM generation_tasks", row=failed_row),
            _CallPattern("UPDATE generation_tasks", row=retried_row),
            _CallPattern("INSERT INTO task_events", row=None),
            _CallPattern("WITH next_task AS", row=reclaimed_row),
            _CallPattern("INSERT INTO task_events", row=None),
        ])

        with patch.object(postgres_queue, "connect", return_value=conn):
            enqueued = postgres_queue.enqueue_task(
                brief_json={"page_count": 4},
                source_text="source content",
                job_id=task_id,
                max_attempts=2,
            )
            self.assertEqual(enqueued["id"], task_id)
            self.assertEqual(enqueued["status"], "queued")

            claimed = postgres_queue.claim_next_task(worker_id="w1")
            self.assertIsNotNone(claimed)
            self.assertEqual(claimed.id, task_id)

            retried = postgres_queue.retry_task(task_id, max_attempts=5)
            self.assertEqual(retried["id"], task_id)
            self.assertEqual(retried["status"], "queued")
            self.assertEqual(retried["max_attempts"], 5)
            self.assertEqual(retried["attempts"], 0)
            self.assertIsNone(retried["error_code"])
            self.assertIsNone(retried["error_message"])

            reclaimed = postgres_queue.claim_next_task(worker_id="w1")
            self.assertIsNotNone(reclaimed)
            self.assertEqual(reclaimed.id, task_id)

        self.assertTrue(conn.committed)


if __name__ == "__main__":
    unittest.main()
