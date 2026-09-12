#!/usr/bin/env python3
"""Dry-run-first cleanup for S3/COS/R2 Cloud Generator artifacts."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean old Cloud Generator artifacts from S3-compatible storage")
    parser.add_argument("--days", type=int, default=int(os.environ.get("PPT_MASTER_ARTIFACT_RETENTION_DAYS", "14")), help="Delete artifacts older than this many days")
    parser.add_argument("--prefix", default=os.environ.get("S3_PREFIX", "ppt-master/artifacts"), help="S3/COS/R2 key prefix")
    parser.add_argument("--apply", action="store_true", help="Actually delete matching objects. Omit for dry-run.")
    parser.add_argument("--limit", type=int, default=1000, help="Max objects to inspect")
    args = parser.parse_args()

    _load_env(ROOT / ".env")
    if os.environ.get("PPT_MASTER_STORAGE_MODE", "local").strip().lower() != "s3":
        print(json.dumps({
            "ok": True,
            "mode": os.environ.get("PPT_MASTER_STORAGE_MODE", "local") or "local",
            "dry_run": not args.apply,
            "message": "PPT_MASTER_STORAGE_MODE is not s3; no remote artifact cleanup needed.",
        }, ensure_ascii=False, indent=2))
        return 0

    try:
        import boto3
    except Exception as exc:
        raise SystemExit(f"boto3 is required for S3-compatible cleanup: {exc}") from exc

    bucket = _env("S3_BUCKET", "AWS_S3_BUCKET", "COS_BUCKET", "R2_BUCKET")
    if not bucket:
        raise SystemExit("S3_BUCKET is required")
    client = boto3.client(
        "s3",
        endpoint_url=_env("S3_ENDPOINT_URL", "AWS_ENDPOINT_URL", "COS_ENDPOINT_URL", "R2_ENDPOINT_URL") or None,
        region_name=_env("S3_REGION", "AWS_REGION", "COS_REGION", "R2_REGION") or None,
        aws_access_key_id=_env("S3_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID", "COS_SECRET_ID", "R2_ACCESS_KEY_ID") or None,
        aws_secret_access_key=_env("S3_SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY", "COS_SECRET_KEY", "R2_SECRET_ACCESS_KEY") or None,
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, args.days))
    paginator = client.get_paginator("list_objects_v2")
    inspected = 0
    candidates: list[dict] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=args.prefix):
        for item in page.get("Contents") or []:
            inspected += 1
            if inspected > args.limit:
                break
            modified = item.get("LastModified")
            if modified and modified < cutoff:
                candidates.append({
                    "Key": item["Key"],
                    "Size": item.get("Size", 0),
                    "LastModified": modified.isoformat(),
                })
        if inspected > args.limit:
            break

    deleted = 0
    if args.apply and candidates:
        for index in range(0, len(candidates), 1000):
            chunk = candidates[index:index + 1000]
            client.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": item["Key"]} for item in chunk]})
            deleted += len(chunk)

    print(json.dumps({
        "ok": True,
        "bucket": bucket,
        "prefix": args.prefix,
        "retention_days": args.days,
        "dry_run": not args.apply,
        "inspected": inspected,
        "candidates": len(candidates),
        "deleted": deleted,
        "sample": candidates[:20],
    }, ensure_ascii=False, indent=2))
    return 0


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


def _env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
