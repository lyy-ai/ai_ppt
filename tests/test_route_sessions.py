import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from cloud_generator.route_sessions import _persist_state, load_route_session


class RouteSessionRecoveryTests(unittest.TestCase):
    def test_interrupted_fill_preparation_is_recovered(self):
        with tempfile.TemporaryDirectory() as temp:
            jobs = Path(temp)
            session_dir = jobs / "_route_sessions" / "route_recover"
            analysis = session_dir / "analysis"
            analysis.mkdir(parents=True)
            (analysis / "source.slide_library.json").write_text("{}\n", encoding="utf-8")
            (analysis / "fill_plan.json").write_text(json.dumps({"operations": []}) + "\n", encoding="utf-8")
            state = {
                "schema": "ppt_master_route_session.v1",
                "session_id": "route_recover",
                "route": "fill_native_pptx",
                "status": "preparing",
                "session_dir": str(session_dir),
                "artifacts": {},
            }
            (session_dir / "route_session.json").write_text(json.dumps(state) + "\n", encoding="utf-8")
            recovered = load_route_session(jobs, "route_recover")
            self.assertEqual(recovered["status"], "awaiting_confirmation")
            self.assertIn("fill_plan", recovered["confirmation"])

    def test_interrupted_preparation_without_artifacts_is_failed(self):
        with tempfile.TemporaryDirectory() as temp:
            jobs = Path(temp)
            session_dir = jobs / "_route_sessions" / "route_failed"
            session_dir.mkdir(parents=True)
            state = {
                "schema": "ppt_master_route_session.v1",
                "session_id": "route_failed",
                "route": "create_template",
                "status": "preparing",
                "session_dir": str(session_dir),
                "artifacts": {},
            }
            (session_dir / "route_session.json").write_text(json.dumps(state) + "\n", encoding="utf-8")
            recovered = load_route_session(jobs, "route_failed")
            self.assertEqual(recovered["status"], "failed")
            self.assertIn("interrupted", recovered["error"])

    @patch.dict(os.environ, {"PPT_MASTER_ROUTE_SESSION_STORE": "postgres", "DATABASE_URL": "postgresql://test"})
    @patch("cloud_generator.postgres_queue.connect")
    def test_postgres_state_store_persists_and_loads_state(self, connect):
        with tempfile.TemporaryDirectory() as temp:
            jobs = Path(temp)
            session_dir = jobs / "_route_sessions" / "route_pg"
            state = {
                "schema": "ppt_master_route_session.v1",
                "session_id": "route_pg",
                "route": "create_template",
                "status": "awaiting_authoring",
                "session_dir": str(session_dir),
                "owner_user_id": "user-1",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "artifacts": {},
            }
            connection = MagicMock()
            connection.__enter__.return_value = connection
            connection.execute.return_value.fetchone.return_value = {"state_json": state}
            connect.return_value = connection
            _persist_state(jobs, state)
            loaded = load_route_session(jobs, "route_pg")
            self.assertEqual(loaded["owner_user_id"], "user-1")
            self.assertGreaterEqual(connection.execute.call_count, 2)
            self.assertTrue(connection.commit.called)
