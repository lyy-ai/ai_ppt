#!/usr/bin/env python3
"""Check Cloud Generator deployment configuration before sharing publicly."""

from __future__ import annotations

import json
import os
import shutil
import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Cloud Generator deployment configuration")
    parser.add_argument(
        "--env-file",
        default=str(ROOT / ".env"),
        help="Environment file to inspect without printing secret values",
    )
    args = parser.parse_args()
    _load_env(Path(args.env_file))
    production = _env("PPT_MASTER_DEPLOYMENT").lower() in {"production", "prod"}
    checks = [
        _check_access_token(),
        _check_auth_required(),
        _check_storage(),
        _check_route_session_store(),
        _check_scanner(),
        _check_costs(),
        _check_payment(),
        _check_email(),
        _check_llm(),
    ]
    if production:
        for item in checks:
            if item["status"] == "warn":
                item["status"] = "fail"
                item["message"] = "Production gate: " + item["message"]
    failed = [item for item in checks if item["status"] == "fail"]
    warnings = [item for item in checks if item["status"] == "warn"]
    print(json.dumps({
        "ok": not failed,
        "profile": "production" if production else "private",
        "failed": len(failed),
        "warnings": len(warnings),
        "checks": checks,
    }, ensure_ascii=False, indent=2))
    return 1 if failed else 0


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _check_access_token() -> dict:
    token = _env("PPT_MASTER_ACCESS_TOKEN")
    if len(token) >= 24 and token.lower() not in {"change-me", "change-me-to-a-long-random-string"}:
        return _ok("access_token", "PPT_MASTER_ACCESS_TOKEN is set")
    if _env("PPT_MASTER_DEPLOYMENT").lower() not in {"production", "prod"}:
        return _warn("access_token", "PPT_MASTER_ACCESS_TOKEN is not set; keep the preview private")
    return _fail("access_token", "Set a long PPT_MASTER_ACCESS_TOKEN before sharing a public URL")


def _check_auth_required() -> dict:
    if _env("PPT_MASTER_REQUIRE_AUTH") == "1":
        return _ok("auth_required", "PPT_MASTER_REQUIRE_AUTH=1")
    return _warn("auth_required", "PPT_MASTER_REQUIRE_AUTH is not enabled")


def _check_storage() -> dict:
    mode = _env("PPT_MASTER_STORAGE_MODE", "local").lower()
    if mode == "local":
        jobs_dir = Path(_env("JOBS_DIR", "")).expanduser()
        if _env("PPT_MASTER_DURABLE_LOCAL_STORAGE") == "1" and jobs_dir.is_absolute() and jobs_dir.exists():
            return _ok("storage", f"durable single-server local artifact storage is configured at {jobs_dir}")
        return _warn("storage", "local artifact storage is OK for private tests but not durable")
    if mode != "s3":
        return _fail("storage", f"Unsupported PPT_MASTER_STORAGE_MODE={mode}")
    missing = [
        name for name in [
            "S3_BUCKET",
            "S3_ENDPOINT_URL",
            "S3_ACCESS_KEY_ID",
            "S3_SECRET_ACCESS_KEY",
        ]
        if not _env(name)
    ]
    if missing:
        return _fail("storage", "Missing S3/COS config: " + ", ".join(missing))
    return _ok("storage", "S3-compatible artifact storage is configured")


def _check_scanner() -> dict:
    scanner = _env("PPT_MASTER_CLAMSCAN_BIN")
    if not scanner:
        return _warn("upload_scanner", "PPT_MASTER_CLAMSCAN_BIN is not set; only file signature checks will run")
    if shutil.which(scanner) or Path(scanner).exists():
        return _ok("upload_scanner", f"scanner found: {scanner}")
    return _fail("upload_scanner", f"PPT_MASTER_CLAMSCAN_BIN does not exist: {scanner}")


def _check_route_session_store() -> dict:
    mode = _env("PPT_MASTER_ROUTE_SESSION_STORE", "json").lower()
    if mode in {"json", "local"}:
        return _warn("route_session_store", "route sessions use local JSON state; configure Postgres for process-independent durable state")
    if mode not in {"postgres", "postgresql"}:
        return _fail("route_session_store", f"Unsupported PPT_MASTER_ROUTE_SESSION_STORE={mode}")
    if not _env("DATABASE_URL"):
        return _fail("route_session_store", "PPT_MASTER_ROUTE_SESSION_STORE=postgres requires DATABASE_URL")
    try:
        import psycopg  # noqa: F401
    except ImportError:
        return _fail("route_session_store", "PPT_MASTER_ROUTE_SESSION_STORE=postgres requires psycopg")
    return _ok("route_session_store", "route sessions use Postgres state storage")


def _check_costs() -> dict:
    names = [
        "PPT_MASTER_COST_PER_PAGE_ATTEMPT_CENTS",
        "PPT_MASTER_COST_PER_1K_PROMPT_TOKENS_CENTS",
        "PPT_MASTER_COST_PER_1K_COMPLETION_TOKENS_CENTS",
        "PPT_MASTER_COST_PER_IMAGE_CENTS",
        "PPT_MASTER_COST_PER_AUDIO_CENTS",
    ]
    missing = [name for name in names if not _env(name)]
    if missing:
        return _warn("cost_telemetry", "Unit costs not fully configured: " + ", ".join(missing))
    return _ok("cost_telemetry", "Page/token/image/audio unit costs are configured")


def _check_payment() -> dict:
    if _env("PPT_MASTER_PAYMENT_ENABLED", "0").lower() not in {"1", "true", "yes", "on"}:
        return _ok("payment", "Payment is disabled by configuration")
    provider = _env("PPT_MASTER_PAYMENT_PROVIDER", "local").lower()
    if provider == "local":
        return _warn("payment", "local mock checkout is configured; use a real payment provider before public launch")
    checkout_template = _env("PPT_MASTER_PAYMENT_CHECKOUT_URL_TEMPLATE")
    if provider in {"aggregator", "aggregate", "ypay", "epay", "xunhupay"}:
        ypay_configured = bool(_env("PPT_MASTER_YPAY_PID") and _env("PPT_MASTER_YPAY_KEY"))
        checkout_template = (
            _env("PPT_MASTER_AGGREGATOR_CHECKOUT_URL_TEMPLATE")
            or checkout_template
        )
        if not ypay_configured and not checkout_template:
            return _fail(
                "payment",
                "Set YPay PID/key or PPT_MASTER_AGGREGATOR_CHECKOUT_URL_TEMPLATE for aggregator checkout",
            )
    if provider == "alipay":
        checkout_template = _env("PPT_MASTER_ALIPAY_CHECKOUT_URL_TEMPLATE") or checkout_template
        if not shutil.which("alipay-bot"):
            return _warn("payment", "PPT_MASTER_PAYMENT_PROVIDER=alipay but alipay-bot is not installed for agent-side payment handling")
    if not checkout_template and not (provider in {"aggregator", "aggregate", "ypay", "epay", "xunhupay"} and ypay_configured):
        if provider == "alipay":
            return _fail("payment", "Set PPT_MASTER_ALIPAY_CHECKOUT_URL_TEMPLATE or PPT_MASTER_PAYMENT_CHECKOUT_URL_TEMPLATE for Alipay checkout")
        return _fail("payment", "Set PPT_MASTER_PAYMENT_CHECKOUT_URL_TEMPLATE for non-local payment provider")
    if not (_env("PPT_MASTER_BILLING_WEBHOOK_SECRET") or _env("PPT_MASTER_BILLING_WEBHOOK_HMAC_SECRET")):
        return _warn("payment", "Payment provider is set but webhook secret/HMAC is not configured")
    return _ok("payment", f"Payment provider contract is configured: {provider}")


def _check_email() -> dict:
    if not _env("PPT_MASTER_SMTP_HOST"):
        return _warn("transactional_email", "SMTP is not configured; team invites will stay in local email_outbox")
    missing = [
        name for name in [
            "PPT_MASTER_SMTP_FROM",
            "PPT_MASTER_SMTP_USERNAME",
            "PPT_MASTER_SMTP_PASSWORD",
        ]
        if not _env(name)
    ]
    if missing:
        return _warn("transactional_email", "SMTP host is set but optional mail fields are missing: " + ", ".join(missing))
    return _ok("transactional_email", "SMTP transactional email is configured")


def _check_llm() -> dict:
    if _env("OPENAI_API_KEY") or _env("DEEPSEEK_API_KEY") or _env("ARK_API_KEY"):
        return _ok("llm", "LLM API key is configured")
    return _warn("llm", "No LLM API key found in environment or .env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _ok(name: str, message: str) -> dict:
    return {"name": name, "status": "ok", "message": message}


def _warn(name: str, message: str) -> dict:
    return {"name": name, "status": "warn", "message": message}


def _fail(name: str, message: str) -> dict:
    return {"name": name, "status": "fail", "message": message}


if __name__ == "__main__":
    raise SystemExit(main())
