#!/usr/bin/env python3
"""Postgres-backed worker for PPT Master generation tasks."""

from __future__ import annotations

import argparse
import json
import os
import socket
import time
import traceback
from pathlib import Path

from .core_adapter import run_core_route
from .postgres_queue import ClaimedTask, claim_next_task, is_task_cancelled, update_task_status


class PostgresJobCancelledError(RuntimeError):
    pass


def _worker_id(value: str | None = None) -> str:
    return value or f"{socket.gethostname()}:{os.getpid()}"


def _project_dir(task: ClaimedTask, jobs_dir: str | Path) -> Path:
    if task.project_dir:
        return Path(task.project_dir)
    return Path(jobs_dir) / task.id


def _output_path(task: ClaimedTask, project_dir: Path) -> Path:
    if task.output_path:
        return Path(task.output_path)
    return project_dir / "output" / "result.pptx"


def _materialize_task_inputs(task: ClaimedTask, project_dir: Path) -> tuple[Path, Path]:
    input_dir = project_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)

    if task.brief_path:
        brief_path = Path(task.brief_path)
    elif task.brief_json is not None:
        brief_path = input_dir / "brief.json"
        brief_path.write_text(json.dumps(task.brief_json, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        raise ValueError("claimed task has neither brief_path nor brief_json")

    if task.source_path:
        source_path = Path(task.source_path)
    elif task.source_text is not None:
        source_path = input_dir / "source.md"
        source_path.write_text(task.source_text, encoding="utf-8")
    else:
        raise ValueError("claimed task has neither source_path nor source_text")

    return brief_path, source_path


def run_claimed_task(task: ClaimedTask, *, jobs_dir: str | Path) -> None:
    project_dir = _project_dir(task, jobs_dir)
    output_path = _output_path(task, project_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        brief_path, source_path = _materialize_task_inputs(task, project_dir)
        if is_task_cancelled(task.id):
            return
        update_task_status(task.id, "generating", message="Generating deck artifacts")
        brief_payload = json.loads(brief_path.read_text(encoding="utf-8"))
        route = str(brief_payload.get("route") or "generate_pptx")
        update_task_status(task.id, "routing", message=f"Routing to PPT Master core: {route}")
        def on_pipeline_status(status: str, message: str, metadata: dict | None = None) -> None:
            if is_task_cancelled(task.id):
                raise PostgresJobCancelledError("Job cancelled by user")
            update_task_status(task.id, status, message=message, metadata=metadata)

        pptx_path = run_core_route(
            route=route,
            brief_path=brief_path,
            source_path=source_path,
            output_path=output_path,
            project_dir=project_dir,
            keep_project=True,
            max_attempts=task.max_attempts,
            status_callback=on_pipeline_status,
        )
        if is_task_cancelled(task.id):
            return
        update_task_status(
            task.id,
            "validating",
            message="Validating generated PPTX",
            metadata={"pptx_path": str(pptx_path)},
        )
        if not Path(pptx_path).exists():
            raise RuntimeError(f"PPTX output does not exist: {pptx_path}")

        update_task_status(
            task.id,
            "completed",
            message="Task completed",
            output_path=str(pptx_path),
            generation_report_path=str(project_dir / "generation_report.json"),
            metadata={
                "pptx_path": str(pptx_path),
                "generation_report": str(project_dir / "generation_report.json"),
            },
        )
    except PostgresJobCancelledError:
        return
    except Exception as exc:
        diagnostics = project_dir / "error_traceback.txt"
        diagnostics.parent.mkdir(parents=True, exist_ok=True)
        diagnostics.write_text(traceback.format_exc(), encoding="utf-8")
        update_task_status(
            task.id,
            "failed",
            message="Task failed",
            error_code=exc.__class__.__name__,
            error_message=str(exc),
            metadata={"diagnostics": str(diagnostics)},
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="PPT Master Postgres worker")
    parser.add_argument("--jobs-dir", default="/tmp/ppt-master-pg-jobs")
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--once", action="store_true", help="Claim and run at most one task")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()

    worker_id = _worker_id(args.worker_id)
    try:
        while True:
            task = claim_next_task(worker_id=worker_id)
            if task:
                run_claimed_task(task, jobs_dir=args.jobs_dir)
                if args.once:
                    return
                continue

            if args.once:
                print(json.dumps({"status": "idle", "worker_id": worker_id}, indent=2))
                return
            time.sleep(args.poll_seconds)
    except Exception as exc:
        print(json.dumps({
            "status": "error",
            "worker_id": worker_id,
            "error_code": exc.__class__.__name__,
            "error_message": str(exc),
        }, ensure_ascii=False, indent=2))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
