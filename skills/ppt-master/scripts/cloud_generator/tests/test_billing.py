import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cloud_generator.api_server import ApiError, _billing_checkout_response, _billing_webhook_response, _payments_enabled


class BillingWebhookTests(unittest.TestCase):
    def test_payments_are_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(_payments_enabled())
        with patch.dict(os.environ, {"PPT_MASTER_PAYMENT_ENABLED": "1"}, clear=True):
            self.assertTrue(_payments_enabled())

    def test_disabled_checkout_does_not_create_a_record(self):
        with tempfile.TemporaryDirectory() as temp:
            jobs = Path(temp)
            (jobs / "_auth").mkdir(parents=True)
            (jobs / "_auth" / "users.json").write_text(json.dumps({
                "users": {"user-1": {"id": "user-1", "plan": "free", "quota": {"date": "", "used": 0}}},
                "sessions": {"session-1": {"user_id": "user-1"}},
                "checkouts": {},
                "billing_events": [],
            }), encoding="utf-8")
            handler = SimpleNamespace(
                jobs_dir=jobs,
                path="/billing/checkout",
                headers={"X-PPT-Master-Session": "session-1", "Host": "ppt.aigcstory.site"},
            )
            with patch.dict(os.environ, {"PPT_MASTER_PAYMENT_ENABLED": "0"}, clear=True):
                with self.assertRaises(ApiError) as raised:
                    _billing_checkout_response(handler, {"plan": "plus"})
            self.assertEqual(raised.exception.status.value, 503)
            data = json.loads((jobs / "_auth" / "users.json").read_text(encoding="utf-8"))
            self.assertEqual(data["checkouts"], {})

    def _handler(self, jobs: Path):
        return SimpleNamespace(jobs_dir=jobs)

    def _seed(self, jobs: Path):
        auth_dir = jobs / "_auth"
        auth_dir.mkdir(parents=True)
        (auth_dir / "users.json").write_text(json.dumps({
            "users": {
                "user-1": {"id": "user-1", "plan": "free", "quota": {"date": "", "used": 0}},
            },
            "checkouts": {
                "checkout-1": {
                    "id": "checkout-1",
                    "user_id": "user-1",
                    "plan": "plus",
                    "amount_cents": 2900,
                    "status": "pending",
                },
            },
            "billing_events": [],
        }), encoding="utf-8")

    def test_completed_webhook_requires_and_validates_amount(self):
        with tempfile.TemporaryDirectory() as temp:
            jobs = Path(temp)
            self._seed(jobs)
            handler = self._handler(jobs)
            with patch.dict(os.environ, {"PPT_MASTER_PAYMENT_ENABLED": "1", "PPT_MASTER_BILLING_WEBHOOK_SECRET": "test-secret"}):
                with self.assertRaises(ApiError):
                    _billing_webhook_response(handler, {"checkout_id": "checkout-1", "secret": "test-secret"})
                result = _billing_webhook_response(handler, {
                    "checkout_id": "checkout-1",
                    "status": "completed",
                    "amount_cents": 2900,
                    "provider_event_id": "event-1",
                    "secret": "test-secret",
                })
            self.assertEqual(result["checkout"]["status"], "completed")
            data = json.loads((jobs / "_auth" / "users.json").read_text(encoding="utf-8"))
            self.assertEqual(data["users"]["user-1"]["plan"], "plus")

    def test_disabled_webhook_cannot_change_membership(self):
        with tempfile.TemporaryDirectory() as temp:
            jobs = Path(temp)
            self._seed(jobs)
            handler = self._handler(jobs)
            with patch.dict(os.environ, {"PPT_MASTER_BILLING_WEBHOOK_SECRET": "test-secret", "PPT_MASTER_PAYMENT_ENABLED": "0"}, clear=True):
                with self.assertRaises(ApiError) as raised:
                    _billing_webhook_response(handler, {
                        "checkout_id": "checkout-1",
                        "status": "completed",
                        "amount_cents": 2900,
                        "secret": "test-secret",
                    })
            self.assertEqual(raised.exception.status.value, 503)
            data = json.loads((jobs / "_auth" / "users.json").read_text(encoding="utf-8"))
            self.assertEqual(data["users"]["user-1"]["plan"], "free")

    def test_duplicate_provider_event_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            jobs = Path(temp)
            self._seed(jobs)
            handler = self._handler(jobs)
            payload = {
                "checkout_id": "checkout-1",
                "status": "completed",
                "amount_cents": 2900,
                "provider_event_id": "event-1",
                "secret": "test-secret",
            }
            with patch.dict(os.environ, {"PPT_MASTER_PAYMENT_ENABLED": "1", "PPT_MASTER_BILLING_WEBHOOK_SECRET": "test-secret"}):
                _billing_webhook_response(handler, payload)
                result = _billing_webhook_response(handler, payload)
            self.assertTrue(result["duplicate"])
            data = json.loads((jobs / "_auth" / "users.json").read_text(encoding="utf-8"))
            self.assertEqual(len(data["billing_events"]), 1)


if __name__ == "__main__":
    unittest.main()
