#!/usr/bin/env python3
"""Clean old local Cloud Generator job workspaces.

Dry-run is the default. Pass --delete to remove matching job directories.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean old PPT Master Cloud Generator jobs")
    parser.add_argument("--jobs-dir", default="/tmp/ppt-master-api-jobs")
    parser.add_argument("--older-than-days", type=float, default=7)
    parser.add_argument("--delete", action="store_true", help="Actually delete matched jobs")
    parser.add_argument("--include-failed", action="store_true", help="Also delete failed/cancelled jobs")
    args = parser.parse_args()

    jobs_dir = Path(args.jobs_dir)
    cutoff = time.time() - args.older_than_days * 86400
    matched: list[dict] = []
    if not jobs_dir.exists():
        print(json.dumps({"jobs_dir": str(jobs_dir), "matched": [], "deleted": 0}, indent=2))
        return 0

    for status_path in sorted(jobs_dir.glob("*/job_status.json")):
        try:
            payload = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        status = str(payload.get("status") or "")
        if status not in {"completed"} and not (args.include_failed and status in {"failed", "cancelled"}):
            continue
        updated = _timestamp(payload.get("updated_at")) or status_path.stat().st_mtime
        if updated > cutoff:
            continue
        job_dir = status_path.parent
        item = {
            "task_id": payload.get("job_id") or job_dir.name,
            "status": status,
            "updated_at": payload.get("updated_at"),
            "path": str(job_dir),
        }
        matched.append(item)
        if args.delete:
            shutil.rmtree(job_dir)

    route_root = jobs_dir / "_route_sessions"
    for state_path in sorted(route_root.glob("*/route_session.json")):
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        status = str(payload.get("status") or "")
        if status not in {"completed", "awaiting_authoring", "awaiting_confirmation", "published", "preparing", "failed", "expired"}:
            continue
        if status in {"failed", "expired"} and not args.include_failed:
            continue
        updated = _timestamp(payload.get("updated_at")) or state_path.stat().st_mtime
        if updated > cutoff:
            continue
        session_dir = state_path.parent
        matched.append({
            "task_id": payload.get("session_id") or session_dir.name,
            "status": status,
            "updated_at": payload.get("updated_at"),
            "path": str(session_dir),
            "kind": "route_session",
        })
        if args.delete:
            shutil.rmtree(session_dir)

    template_root = jobs_dir / "_templates"
    for metadata_path in sorted(template_root.glob("*/template.json")):
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        updated = _timestamp(payload.get("created_at")) or metadata_path.stat().st_mtime
        if updated > cutoff:
            continue
        template_dir = metadata_path.parent
        matched.append({
            "task_id": payload.get("template_id") or template_dir.name,
            "status": "published",
            "updated_at": payload.get("created_at"),
            "path": str(template_dir),
            "kind": "published_template",
        })
        if args.delete:
            shutil.rmtree(template_dir)

    print(json.dumps({
        "jobs_dir": str(jobs_dir),
        "older_than_days": args.older_than_days,
        "dry_run": not args.delete,
        "matched": matched,
        "deleted": len(matched) if args.delete else 0,
    }, ensure_ascii=False, indent=2))
    return 0


def _timestamp(value: object) -> float:
    if not value:
        return 0.0
    try:
        from datetime import datetime
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        return 0.0


if __name__ == "__main__":
    raise SystemExit(main())
