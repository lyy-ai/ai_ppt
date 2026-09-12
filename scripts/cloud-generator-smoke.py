#!/usr/bin/env python3
"""Smoke-test the local Cloud Generator API without spending real model calls."""

from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


ROOT = Path(__file__).resolve().parents[1]
PYTHONPATH = str(ROOT / "skills" / "ppt-master" / "scripts")
ACCESS_TOKEN = "smoke-token"


def _alipay_test_env() -> dict[str, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return {
        "PPT_MASTER_ALIPAY_APP_ID": "2026000000000000",
        "PPT_MASTER_ALIPAY_PRIVATE_KEY": private_pem,
        "PPT_MASTER_ALIPAY_PUBLIC_KEY": public_pem,
        "PPT_MASTER_ALIPAY_GATEWAY": "https://openapi.alipay.test/gateway.do",
        "PPT_MASTER_ALIPAY_NOTIFY_URL": "http://127.0.0.1/alipay/notify",
        "PPT_MASTER_ALIPAY_RETURN_URL": "http://127.0.0.1:8000/cloud-generator.html",
    }


ALIPAY_TEST_ENV = _alipay_test_env()


def main() -> int:
    jobs_dir = Path(tempfile.mkdtemp(prefix="ppt-master-cloud-smoke-"))
    port = _free_port()
    server_log = jobs_dir / "api-server.log"
    server = _start_server(port, jobs_dir, server_log)
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_health(base, server, server_log)
        _assert_security_checks()
        _assert_svg_checker_accepts_single_quoted_viewbox()
        session = _register(base)
        provider_status = _json("GET", base, "/provider-status", session=session)
        _assert("image" in provider_status and "audio" in provider_status, "provider status should expose image/audio sections")
        _assert("billing" in provider_status and "email" in provider_status, "provider status should expose billing/email sections")
        _assert((provider_status["billing"].get("credit_plans") or {}).get("pro") == 1000, "provider status should expose credit plans")
        _assert((provider_status["audio"].get("providers") or {}).get("edge", {}).get("configured") is True, "edge audio should be configured by default")
        checkout = _json("POST", base, "/billing/checkout", {"plan": "pro", "auto_complete": True, "region": "cn", "currency": "CNY", "market_payment_provider": "aggregator"}, session=session)
        _assert(checkout["checkout"]["status"] == "completed", "checkout should complete locally")
        _assert(checkout["checkout"]["currency"] == "CNY", "checkout should preserve regional currency")
        _assert(checkout["billing"]["payment_provider"] == "aggregator", "checkout should expose market payment provider")
        _assert(checkout["membership"]["plan"] == "pro", "membership plan should switch to pro")
        _assert_referral_contract(base, session)
        _assert_anonymous_blocked(base)
        task_id = _create_task(base, session)
        task = _wait_task(base, task_id, session)
        _assert(task["status"] == "completed", f"task should complete, got {task['status']}")
        _assert(task.get("quota_debited") is True, "task should debit quota")
        _assert((task.get("metrics") or {}).get("model_profiles", {}).get("slide") == 4, "model profile metrics missing")
        _add_fake_image_asset(jobs_dir, task_id)
        preview = _json("GET", base, f"/generation-tasks/{task_id}/preview", session=session)
        _assert(len(preview.get("images") or []) == 1, "preview should expose image assets")
        artifacts = _json("GET", base, f"/generation-tasks/{task_id}/artifacts", session=session)
        _assert(len(artifacts.get("artifacts") or []) >= 6, "expected pptx + preview + image artifacts")
        _assert_forbidden_for_other_user(base, task_id)
        _force_failed(jobs_dir, task_id)
        retry = _json("POST", base, f"/generation-tasks/{task_id}/retry", {"max_attempts": 5}, session=session)
        _assert(retry["status"] == "queued", "retry should requeue failed task")
        _assert((retry.get("retry_fallback") or {}).get("changed") is True, "retry should apply safe fallback")
        _assert_retry_brief_fallback(jobs_dir, task_id)
        task = _wait_task(base, task_id, session)
        _assert(task["status"] == "completed", f"retried task should complete: {task.get('status')} {task.get('error_message')}")
        membership = _json("GET", base, "/membership", session=session)
        _assert(membership["quota"]["unit"] == "credits", "quota should now be credit-based")
        _assert(membership["quota"]["base_limit"] == 1000, "pro base credit limit should be 1000")
        _assert(membership["quota"]["bonus"] >= 50, "referrer bonus credits should be tracked separately")
        _assert(membership["quota"]["limit"] == 1000 + membership["quota"]["bonus"], "total credit limit should include bonus credits")
        _assert(membership["quota"]["used"] == 10, "retry should not double debit credits")
        usage = _json("GET", base, "/usage", session=session)
        _assert(usage["usage"]["completed"] >= 1, "usage should include completed task")
        _assert(usage["usage"]["page_attempts"] >= 4, "usage should include page attempts")
        _assert(usage["usage"].get("image_asset_count", 0) >= 1, "usage should include image asset count")
        _assert(usage["usage"].get("llm_call_count", 0) >= 5, "usage should include LLM call count")
        _assert(usage["usage"].get("total_tokens", 0) > 0, "usage should include total tokens")
        _assert(usage["usage"].get("llm_usage_by_model"), "usage should include model-level token breakdown")
        _assert(usage["usage"].get("estimated_cost_cents", 0) > 0, "usage should include estimated cost")
        _assert_team_contract(base, session)
        _assert_logout_contract(base, session)
        print(json.dumps({
            "ok": True,
            "port": port,
            "jobs_dir": str(jobs_dir),
            "task_id": task_id,
            "artifacts": len(artifacts.get("artifacts") or []),
            "quota": membership["quota"],
            "usage": usage["usage"],
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()


def _start_server(port: int, jobs_dir: Path, log_path: Path) -> subprocess.Popen:
    env = os.environ.copy()
    env["CLOUD_GENERATOR_MOCK"] = "1"
    env["PPT_MASTER_REQUIRE_AUTH"] = "1"
    env["PPT_MASTER_COST_PER_PAGE_ATTEMPT_CENTS"] = "0.02"
    env["PPT_MASTER_COST_PER_1K_PROMPT_TOKENS_CENTS"] = "0.02"
    env["PPT_MASTER_COST_PER_1K_COMPLETION_TOKENS_CENTS"] = "0.08"
    env["PPT_MASTER_COST_PER_IMAGE_CENTS"] = "1.00"
    env["PPT_MASTER_COST_PER_AUDIO_CENTS"] = "0.20"
    env["PPT_MASTER_ALIPAY_CHECKOUT_URL_TEMPLATE"] = "https://pay.example.test/alipay?checkout_id={checkout_id}&plan={plan}&email={email}"
    env["PPT_MASTER_AGGREGATOR_CHECKOUT_URL_TEMPLATE"] = "https://pay.example.test/aggregator?out_trade_no={out_trade_no}&amount={total_amount}&notify_url={notify_url}&return_url={return_url}"
    env["PPT_MASTER_YPAY_BASE_URL"] = "https://pay.example.test"
    env["PPT_MASTER_YPAY_PID"] = "199"
    env["PPT_MASTER_YPAY_KEY"] = "iyNMRtjaYUJxL4DvuXemd2kV3EO8TWFs"
    env["PPT_MASTER_YPAY_NOTIFY_URL"] = "http://127.0.0.1/ypay/notify"
    env["PPT_MASTER_YPAY_RETURN_URL"] = "http://127.0.0.1:8000/cloud-generator.html"
    env.update(ALIPAY_TEST_ENV)
    env["PYTHONPATH"] = PYTHONPATH
    log_file = log_path.open("w", encoding="utf-8")
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "cloud_generator.api_server",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--store",
            "json",
            "--jobs-dir",
            str(jobs_dir),
            "--access-token",
            ACCESS_TOKEN,
            "--frontend-base-url",
            "http://127.0.0.1:8000",
        ],
        cwd=str(ROOT),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )


def _wait_health(base: str, server: subprocess.Popen, log_path: Path) -> None:
    deadline = time.time() + 30
    while time.time() < deadline:
        if server.poll() is not None:
            log_tail = _tail_text(log_path)
            raise RuntimeError(f"API server exited before becoming healthy:\n{log_tail}")
        try:
            health = _json("GET", base, "/health")
            if health.get("status") == "ok":
                return
        except Exception:
            time.sleep(0.2)
    log_tail = _tail_text(log_path)
    raise RuntimeError(f"API server did not become healthy:\n{log_tail}")


def _tail_text(path: Path, limit: int = 4000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return "(server log not found)"
    return text[-limit:] or "(server log is empty)"


def _assert_svg_checker_accepts_single_quoted_viewbox() -> None:
    with tempfile.TemporaryDirectory(prefix="ppt-master-svg-check-") as tmp:
        svg_path = Path(tmp) / "single_quote_viewbox.svg"
        svg_path.write_text(
            "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1280 720' width='1280' height='720'>"
            "<rect x='0' y='0' width='1280' height='720' fill='#ffffff'/>"
            "<text x='80' y='120' font-family='Arial, Microsoft YaHei, sans-serif' font-size='40' fill='#111111'>Smoke</text>"
            "</svg>",
            encoding="utf-8",
        )
        result = subprocess.run(
            [sys.executable, str(ROOT / "skills" / "ppt-master" / "scripts" / "svg_quality_checker.py"), str(svg_path)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        _assert(result.returncode == 0, f"single-quoted viewBox SVG should pass quality checker:\n{result.stdout}\n{result.stderr}")


def _register(base: str, referral_code: str = "") -> str:
    payload = {"email": f"smoke-{uuid.uuid4().hex[:12]}@example.com", "password": "pptmaster", "auto_register": True}
    if referral_code:
        payload["referral_code"] = referral_code
    data = _json("POST", base, "/auth/login", payload, include_access=False)
    _assert(data.get("session_token"), "auth should return session token")
    return data["session_token"]


def _assert_referral_contract(base: str, session: str) -> None:
    referrals = _json("GET", base, "/referrals", session=session)
    code = referrals.get("referral_code")
    _assert(code and "ref=" in referrals.get("invite_url", ""), "referrals should expose invite link")
    before = _json("GET", base, "/membership", session=session)["credits"]["remaining"]
    referred_session = _register(base, referral_code=code)
    referred_membership = _json("GET", base, "/membership", session=referred_session)
    _assert(referred_membership["credits"]["bonus"] >= 30, "referred signup should receive bonus credits")
    _assert(referred_membership["credits"]["remaining"] >= 60, "referred signup should stack free and bonus credits")
    referred_task = _create_task(base, referred_session)
    task = _wait_task(base, referred_task, referred_session)
    _assert(task["status"] == "completed", "referred user's first generation should complete")
    _json("GET", base, f"/generation-tasks/{referred_task}", session=referred_session)
    after = _json("GET", base, "/membership", session=session)["credits"]["remaining"]
    _assert(after == before + 50, "referrer should receive first-generation bonus credits")


def _assert_anonymous_blocked(base: str) -> None:
    status, data = _request("POST", base, "/generation-tasks", {
        "brief": {"page_count": 4, "source_text": "x"},
        "source_text": "x",
    }, session="")
    _assert(status == 401, f"anonymous create should be blocked, got {status}: {data}")


def _create_task(base: str, session: str) -> str:
    task_id = f"smoke_{int(time.time())}"
    payload = {
        "job_id": task_id,
        "run_async": True,
        "brief": {
            "page_count": 4,
            "language": "zh-CN",
            "source_text": "生成 4 页 smoke PPT",
        },
        "source_text": "生成 4 页 smoke PPT",
    }
    data = _json("POST", base, "/generation-tasks", payload, session=session)
    _assert(data.get("task_id") == task_id, "task id mismatch")
    return task_id


def _wait_task(base: str, task_id: str, session: str) -> dict:
    deadline = time.time() + 15
    last = {}
    while time.time() < deadline:
        last = _json("GET", base, f"/generation-tasks/{task_id}", session=session)
        if last.get("status") in {"completed", "failed", "cancelled"}:
            return last
        time.sleep(0.3)
    raise RuntimeError(f"task did not finish: {last}")


def _assert_forbidden_for_other_user(base: str, task_id: str) -> None:
    other = _register(base)
    status, data = _request("GET", base, f"/generation-tasks/{task_id}", session=other)
    _assert(status == 403, f"other user should be forbidden, got {status}: {data}")


def _assert_team_contract(base: str, session: str) -> None:
    aggregate = _json("POST", base, "/billing/checkout", {"plan": "pro", "provider": "aggregator"}, session=session)
    _assert(aggregate["checkout"]["status"] == "pending", "aggregator checkout should stay pending")
    _assert(aggregate["checkout"]["provider"] == "aggregator", "aggregator checkout should expose provider")
    _assert("out_trade_no=" in aggregate.get("checkout_url", ""), "aggregator checkout URL should include order id")
    _assert("submit.php" in aggregate.get("checkout_url", "") and "sign=" in aggregate.get("checkout_url", ""), "YPay checkout URL should be signed")
    aggregate_done = _json("POST", base, "/billing/webhook", {"checkout_id": aggregate["checkout"]["id"], "status": "completed"}, session=session)
    _assert(aggregate_done["checkout"]["status"] == "completed", "generic billing webhook should complete aggregator checkout")
    external = _json("POST", base, "/billing/checkout", {"plan": "pro", "provider": "alipay"}, session=session)
    _assert(external["checkout"]["status"] == "pending", "alipay checkout should stay pending")
    _assert(external["checkout"].get("payment_skill") == "alipay-payment-skill", "alipay checkout should expose payment skill metadata")
    _assert("alipay.trade.page.pay" in urllib.parse.unquote(external.get("checkout_url", "")), "alipay checkout URL should use official page pay")
    _assert("sign=" in external.get("checkout_url", ""), "alipay checkout URL should be signed")
    _assert_alipay_notify_contract(base, session)
    completed = _json("POST", base, "/billing/webhook", {"checkout_id": external["checkout"]["id"], "status": "completed"}, session=session)
    _assert(completed["checkout"]["status"] == "completed", "billing webhook should complete external checkout")
    _assert_ypay_notify_contract(base, session)
    checkout = _json("POST", base, "/billing/checkout", {"plan": "team", "auto_complete": True}, session=session)
    _assert(checkout["membership"]["plan"] == "team", "membership plan should switch to team")
    team = _json("GET", base, "/team", session=session)
    team_payload = team.get("team") or {}
    _assert(team_payload.get("team_id"), "team plan should create a team")
    _assert(len(team_payload.get("members") or []) == 1, "team should include owner")
    invited = _json("POST", base, "/team/invite", {"email": "teammate@example.com", "role": "member"}, session=session)
    _assert(any(m.get("email") == "teammate@example.com" for m in invited["team"].get("members") or []), "team invite should add member")
    _assert((invited.get("invite_delivery") or {}).get("status") == "queued", "team invite should create local email outbox entry")
    removed = _json("POST", base, "/team/remove", {"email": "teammate@example.com"}, session=session)
    _assert(not any(m.get("email") == "teammate@example.com" for m in removed["team"].get("members") or []), "team remove should remove member")


def _assert_alipay_notify_contract(base: str, session: str) -> None:
    before = _json("GET", base, "/membership", session=session)
    _assert(before["plan"] == "pro", "test user should be pro before alipay team checkout")
    team_checkout = _json("POST", base, "/billing/checkout", {"plan": "team", "provider": "alipay"}, session=session)
    payload = {
        "app_id": ALIPAY_TEST_ENV["PPT_MASTER_ALIPAY_APP_ID"],
        "notify_id": f"notify_{uuid.uuid4().hex[:12]}",
        "out_trade_no": team_checkout["checkout"]["id"],
        "trade_no": f"trade_{uuid.uuid4().hex[:12]}",
        "trade_status": "TRADE_SUCCESS",
        "total_amount": team_checkout["checkout"]["total_amount"],
        "buyer_id": "2088000000000000",
    }
    payload["sign_type"] = "RSA2"
    payload["sign"] = _alipay_sign(payload)
    status, body = _form_request(base, "/billing/alipay/notify", payload)
    _assert(status == 200 and body == "success", f"alipay notify should return success, got {status}: {body}")
    after = _json("GET", base, "/membership", session=session)
    _assert(after["plan"] == "team", "alipay notify should upgrade membership")
    repeat_status, repeat_body = _form_request(base, "/billing/alipay/notify", payload)
    _assert(repeat_status == 200 and repeat_body == "success", "alipay notify should be idempotent")


def _assert_ypay_notify_contract(base: str, session: str) -> None:
    checkout = _json("POST", base, "/billing/checkout", {"plan": "team", "provider": "aggregator"}, session=session)
    payload = {
        "pid": "199",
        "out_trade_no": checkout["checkout"]["id"],
        "trade_no": f"ypay_{uuid.uuid4().hex[:12]}",
        "orderid_wx_al": f"ali_{uuid.uuid4().hex[:12]}",
        "type": "alipay",
        "name": "PPT Master TEAM",
        "money": checkout["checkout"]["total_amount"],
        "trade_status": "TRADE_SUCCESS",
        "param": checkout["checkout"]["id"],
    }
    payload["sign"] = _ypay_sign(payload)
    payload["sign_type"] = "MD5"
    status, body = _raw_get(base, f"/billing/ypay/notify?{urllib.parse.urlencode(payload)}")
    _assert(status == 200 and body == "success", f"YPay notify should return success, got {status}: {body}")
    after = _json("GET", base, "/membership", session=session)
    _assert(after["plan"] == "team", "YPay notify should upgrade membership")
    repeat_status, repeat_body = _raw_get(base, f"/billing/ypay/notify?{urllib.parse.urlencode(payload)}")
    _assert(repeat_status == 200 and repeat_body == "success", "YPay notify should be idempotent")


def _assert_logout_contract(base: str, session: str) -> None:
    logout = _json("POST", base, "/auth/logout", {}, session=session)
    _assert(logout.get("ok") is True and logout.get("logged_out") is True, "logout should revoke session")
    status, data = _request("GET", base, "/usage", session=session)
    _assert(status == 401, f"logged out session should be unauthorized, got {status}: {data}")


def _force_failed(jobs_dir: Path, task_id: str) -> None:
    status_path = jobs_dir / task_id / "job_status.json"
    state = json.loads(status_path.read_text(encoding="utf-8"))
    state["status"] = "failed"
    state["error_code"] = "SMOKE_FORCED_FAILURE"
    state["error_message"] = "forced failure for retry smoke"
    state["events"].append({
        "status": "failed",
        "message": "forced failure for retry smoke",
        "created_at": state["updated_at"],
        "metadata": {},
    })
    status_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    brief_path = jobs_dir / task_id / "input" / "brief.json"
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    brief.update({
        "include_images": True,
        "include_audio": True,
        "include_animations": True,
        "include_svg_snapshot": True,
        "image_source_mode": "auto",
    })
    brief_path.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")


def _add_fake_image_asset(jobs_dir: Path, task_id: str) -> None:
    images_dir = jobs_dir / task_id / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    tiny_png = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    )
    (images_dir / "01_image.png").write_bytes(base64.b64decode(tiny_png))
    (images_dir / "image_sources.json").write_text(json.dumps({
        "01_image.png": {
            "slide": 1,
            "source": "smoke",
            "query": "test image",
            "attribution": "smoke asset",
        }
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def _assert_retry_brief_fallback(jobs_dir: Path, task_id: str) -> None:
    brief_path = jobs_dir / task_id / "input" / "brief.json"
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    _assert(brief.get("include_images") is False, "retry fallback should disable images")
    _assert(brief.get("include_audio") is False, "retry fallback should disable audio")
    _assert(brief.get("include_animations") is False, "retry fallback should disable animations")
    _assert(brief.get("include_svg_snapshot") is False, "retry fallback should disable svg snapshot")
    _assert(brief.get("image_source_mode") == "none", "retry fallback should set image_source_mode=none")


def _assert_security_checks() -> None:
    sys.path.insert(0, PYTHONPATH)
    from cloud_generator.source_materials import _normalize_urls, materialize_source
    from cloud_generator.security import redact_secrets
    from cloud_generator.api_server import _apply_retry_fallback

    os.environ["SMOKE_API_KEY"] = "sk-smoke-secret-value"
    redacted = redact_secrets("Authorization: Bearer live-secret-token-12345\nkey=sk-smoke-secret-value")
    _assert("live-secret-token-12345" not in redacted, "bearer token should be redacted")
    _assert("sk-smoke-secret-value" not in redacted, "env secret should be redacted")

    for url in ["http://127.0.0.1:8000/x", "http://localhost/x", "http://10.0.0.1/x"]:
        try:
            _normalize_urls([url])
        except Exception:
            pass
        else:
            raise AssertionError(f"private URL should be blocked: {url}")

    materialize_source(
        input_dir=Path(tempfile.mkdtemp(prefix="ppt-master-smoke-material-")),
        source_text="ok",
        materials=[],
    )
    try:
        materialize_source(
            input_dir=Path(tempfile.mkdtemp(prefix="ppt-master-smoke-material-")),
            materials=[{"name": "bad.exe", "content_base64": base64.b64encode(b"x").decode("ascii")}],
        )
    except Exception:
        pass
    else:
        raise AssertionError("unsupported upload should be blocked")

    for name, content in [
        ("fake.pdf", b"MZ disguised executable"),
        ("fake.docx", b"not-a-zip"),
    ]:
        try:
            materialize_source(
                input_dir=Path(tempfile.mkdtemp(prefix="ppt-master-smoke-material-")),
                materials=[{"name": name, "content_base64": base64.b64encode(content).decode("ascii")}],
            )
        except Exception:
            pass
        else:
            raise AssertionError(f"signature check should block {name}")

    brief_path = Path(tempfile.mkdtemp(prefix="ppt-master-smoke-retry-")) / "brief.json"
    brief_path.write_text(json.dumps({
        "page_count": 20,
        "include_images": True,
        "include_audio": True,
        "include_svg_snapshot": True,
        "include_animations": True,
        "image_source_mode": "generate",
    }), encoding="utf-8")
    _apply_retry_fallback(brief_path, {
        "fallback_mode": "safe",
        "fallback_max_pages": 10,
        "disable_images_on_retry": False,
        "disable_audio_on_retry": False,
        "disable_svg_snapshot_on_retry": False,
        "disable_animations_on_retry": False,
    })
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    _assert(brief["page_count"] == 10, "retry fallback should still cap pages")
    _assert(brief["include_images"] is True, "advanced retry should preserve images when requested")
    _assert(brief["include_audio"] is True, "advanced retry should preserve audio when requested")
    _assert(brief["include_svg_snapshot"] is True, "advanced retry should preserve svg snapshot when requested")
    _assert(brief["include_animations"] is True, "advanced retry should preserve animations when requested")


def _json(method: str, base: str, path: str, payload: dict | None = None, *, session: str = "", include_access: bool = True) -> dict:
    status, data = _request(method, base, path, payload, session=session, include_access=include_access)
    if not (200 <= status < 300):
        raise RuntimeError(f"{method} {path} failed: HTTP {status}: {data}")
    return data


def _alipay_sign_content(payload: dict) -> str:
    return "&".join(
        f"{key}={payload[key]}"
        for key in sorted(payload)
        if key not in {"sign", "sign_type"} and payload.get(key) not in {None, ""}
    )


def _alipay_sign(payload: dict) -> str:
    private_key = serialization.load_pem_private_key(
        ALIPAY_TEST_ENV["PPT_MASTER_ALIPAY_PRIVATE_KEY"].encode("utf-8"),
        password=None,
    )
    signature = private_key.sign(
        _alipay_sign_content(payload).encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("ascii")


def _ypay_sign(payload: dict) -> str:
    content = "&".join(
        f"{key}={payload[key]}"
        for key in sorted(payload)
        if key not in {"sign", "sign_type"} and payload.get(key) not in {None, ""}
    )
    return __import__("hashlib").md5(f"{content}iyNMRtjaYUJxL4DvuXemd2kV3EO8TWFs".encode("utf-8")).hexdigest()


def _raw_get(base: str, path: str) -> tuple[int, str]:
    request = urllib.request.Request(base + path, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def _form_request(base: str, path: str, payload: dict) -> tuple[int, str]:
    body = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        base + path,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def _request(method: str, base: str, path: str, payload: dict | None = None, *, session: str = "", include_access: bool = True) -> tuple[int, dict]:
    headers = {}
    body = None
    if include_access:
        headers["X-PPT-Master-Token"] = ACCESS_TOKEN
    if session:
        headers["X-PPT-Master-Session"] = session
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(base + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw or "{}")
        except Exception:
            data = {"raw": raw}
        return exc.code, data


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
