#!/usr/bin/env python3
"""Local worker job runner for the cloud generator prototype.

This module wraps the command pipeline with a job-shaped status contract:

queued -> running -> completed
                  -> failed

It intentionally stores state in JSON files so the prototype can run without a
database. A production API can map the same fields to generation_tasks and
task_events tables.
"""

from __future__ import annotations

import argparse
import json
import os
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core_adapter import run_core_route
from .security import redact_secrets


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class JobCancelledError(RuntimeError):
    """Raised when an in-flight local job is cancelled by its owner."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class JobEvent:
    status: str
    message: str
    created_at: str = field(default_factory=_now)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class JobStatus:
    job_id: str
    status: str
    created_at: str
    updated_at: str
    brief_path: str
    source_path: str
    output_path: str
    project_dir: str
    owner_user_id: str = ""
    quota_debited: bool = False
    quota_debit_credits: int = 0
    quota_credit_cost_model: str = ""
    quota_refunded: bool = False
    quota_refund_credits: int = 0
    error_code: str = ""
    error_message: str = ""
    failure_analysis: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    events: list[JobEvent] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["events"] = [asdict(event) for event in self.events]
        return data


def _write_status(job: JobStatus) -> Path:
    job.updated_at = _now()
    path = Path(job.project_dir) / "job_status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(job.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _transition(
    job: JobStatus,
    status: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    job.status = status
    job.events.append(JobEvent(status=status, message=message, metadata=metadata or {}))
    _write_status(job)


def _timestamp_seconds(value: str) -> float:
    try:
        return datetime.fromisoformat(value).timestamp()
    except Exception:
        return 0.0


def _collect_job_metrics(job: JobStatus) -> dict[str, Any]:
    project_dir = Path(job.project_dir)
    output_path = Path(job.output_path)
    metrics: dict[str, Any] = {
        "event_count": len(job.events),
    }
    started_at = next((event.created_at for event in job.events if event.status == "running"), job.created_at)
    if started_at:
        start = _timestamp_seconds(started_at)
        end = _timestamp_seconds(job.updated_at) or datetime.now(timezone.utc).timestamp()
        if start:
            metrics["duration_seconds"] = round(max(0.0, end - start), 3)
    report_path = project_dir / "generation_report.json"
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            metrics["page_count_expected"] = report.get("page_count_expected")
            metrics["page_count_generated"] = report.get("page_count_generated")
            profile_counts: dict[str, int] = {}
            model_counts: dict[str, int] = {}
            page_attempts = 0
            for page in report.get("pages") or []:
                attempts = page.get("attempts") or []
                page_attempts += len(attempts)
                for attempt in attempts:
                    profile = str(attempt.get("model_profile") or "").strip()
                    if profile:
                        profile_counts[profile] = profile_counts.get(profile, 0) + 1
                    model_name = str(attempt.get("model_name") or "").strip()
                    if model_name:
                        model_counts[model_name] = model_counts.get(model_name, 0) + 1
            metrics["page_attempts"] = page_attempts
            if profile_counts:
                metrics["model_profiles"] = profile_counts
            if model_counts:
                metrics["model_names"] = model_counts
        except Exception:
            pass
    if output_path.exists():
        metrics["pptx_size_bytes"] = output_path.stat().st_size
    image_count = _count_assets(project_dir / "images", {".png", ".jpg", ".jpeg", ".webp", ".gif"})
    if image_count:
        metrics["image_asset_count"] = image_count
    audio_count = _count_assets(project_dir / "audio", {".mp3", ".wav", ".m4a"})
    if audio_count:
        metrics["audio_asset_count"] = audio_count
    metrics.update(_collect_llm_usage_metrics(project_dir / "llm_usage.jsonl"))
    estimated_cost = _estimate_cost_cents(metrics)
    if estimated_cost:
        metrics["estimated_cost_cents"] = estimated_cost
    return {key: value for key, value in metrics.items() if value is not None}


def _count_assets(directory: Path, suffixes: set[str]) -> int:
    if not directory.exists() or not directory.is_dir():
        return 0
    return sum(1 for path in directory.iterdir() if path.is_file() and path.suffix.lower() in suffixes)


def _env_float(name: str, default: float = 0.0) -> float:
    try:
        return float(os.environ.get(name, str(default)) or default)
    except Exception:
        return default


def _usage_int(usage: dict[str, Any], *names: str) -> int:
    for name in names:
        value = usage.get(name)
        if isinstance(value, (int, float)):
            return int(value)
    return 0


def _add_usage_bucket(bucket: dict[str, Any], *, prompt: int, completion: int, total: int) -> None:
    bucket["calls"] = int(bucket.get("calls") or 0) + 1
    bucket["prompt_tokens"] = int(bucket.get("prompt_tokens") or 0) + prompt
    bucket["completion_tokens"] = int(bucket.get("completion_tokens") or 0) + completion
    bucket["total_tokens"] = int(bucket.get("total_tokens") or 0) + total


def _collect_llm_usage_metrics(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    call_count = 0
    by_model: dict[str, dict[str, Any]] = {}
    by_profile: dict[str, dict[str, Any]] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        try:
            entry = json.loads(raw_line)
        except Exception:
            continue
        usage = entry.get("usage") if isinstance(entry, dict) else None
        if not isinstance(usage, dict):
            continue
        prompt = _usage_int(usage, "prompt_tokens", "input_tokens")
        completion = _usage_int(usage, "completion_tokens", "output_tokens")
        total = _usage_int(usage, "total_tokens") or prompt + completion
        prompt_tokens += prompt
        completion_tokens += completion
        total_tokens += total
        call_count += 1
        model = str(entry.get("model") or "unknown").strip() or "unknown"
        profile = str(entry.get("profile") or "default").strip() or "default"
        _add_usage_bucket(by_model.setdefault(model, {}), prompt=prompt, completion=completion, total=total)
        _add_usage_bucket(by_profile.setdefault(profile, {}), prompt=prompt, completion=completion, total=total)
    if not call_count:
        return {}
    return {
        "llm_call_count": call_count,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "llm_usage_by_model": by_model,
        "llm_usage_by_profile": by_profile,
    }


def _estimate_cost_cents(metrics: dict[str, Any]) -> float:
    page_attempt_cost = int(metrics.get("page_attempts") or 0) * _env_float("PPT_MASTER_COST_PER_PAGE_ATTEMPT_CENTS")
    image_cost = int(metrics.get("image_asset_count") or 0) * _env_float("PPT_MASTER_COST_PER_IMAGE_CENTS")
    audio_cost = int(metrics.get("audio_asset_count") or 0) * _env_float("PPT_MASTER_COST_PER_AUDIO_CENTS")
    prompt_cost = int(metrics.get("prompt_tokens") or 0) / 1000 * _env_float("PPT_MASTER_COST_PER_1K_PROMPT_TOKENS_CENTS")
    completion_cost = int(metrics.get("completion_tokens") or 0) / 1000 * _env_float("PPT_MASTER_COST_PER_1K_COMPLETION_TOKENS_CENTS")
    total_cost = int(metrics.get("total_tokens") or 0) / 1000 * _env_float("PPT_MASTER_COST_PER_1K_TOKENS_CENTS")
    return round(page_attempt_cost + image_cost + audio_cost + prompt_cost + completion_cost + total_cost, 4)


def create_job(
    *,
    brief_path: str | Path,
    source_path: str | Path,
    jobs_dir: str | Path,
    job_id: str | None = None,
    owner_user_id: str = "",
    quota_debited: bool = False,
    quota_debit_credits: int = 0,
    quota_credit_cost_model: str = "",
) -> JobStatus:
    """Create a local job workspace and queued status file."""
    job_id = job_id or f"job_{uuid.uuid4().hex[:12]}"
    jobs_dir = Path(jobs_dir)
    project_dir = jobs_dir / job_id
    output_path = project_dir / "output" / "result.pptx"
    created_at = _now()

    job = JobStatus(
        job_id=job_id,
        status="queued",
        created_at=created_at,
        updated_at=created_at,
        brief_path=str(Path(brief_path).resolve()),
        source_path=str(Path(source_path).resolve()),
        output_path=str(output_path),
        project_dir=str(project_dir),
        owner_user_id=owner_user_id,
        quota_debited=quota_debited,
        quota_debit_credits=quota_debit_credits,
        quota_credit_cost_model=quota_credit_cost_model,
        events=[JobEvent(status="queued", message="Job created")],
    )
    _write_status(job)
    return job


def reset_job_for_retry(job: JobStatus) -> JobStatus:
    """Reset a terminal local job so the worker can run it again."""
    if job.status not in TERMINAL_STATUSES:
        return job
    job.status = "queued"
    job.error_code = ""
    job.error_message = ""
    job.failure_analysis = {}
    job.metrics = {}
    job.events.append(JobEvent(status="queued", message="Job queued for retry"))
    output_path = Path(job.output_path)
    if output_path.exists():
        output_path.unlink()
    _write_status(job)
    return job


def cancel_job(job: JobStatus) -> JobStatus:
    """Mark a queued or running local job as cancelled."""
    if job.status in TERMINAL_STATUSES:
        return job
    job.error_code = "cancelled"
    job.error_message = "Job cancelled by user"
    job.failure_analysis = {}
    _transition(job, "cancelled", "Job cancelled by user")
    return job


def run_job(job: JobStatus, *, max_attempts: int = 2) -> JobStatus:
    """Run a queued local job to completion."""
    if job.status in TERMINAL_STATUSES:
        return job

    project_dir = Path(job.project_dir)
    output_path = Path(job.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        _transition(job, "running", "Pipeline started", {"max_attempts": max_attempts})

        def on_pipeline_status(status: str, message: str, metadata: dict | None = None) -> None:
            latest = load_job(Path(job.project_dir) / "job_status.json")
            if latest.status == "cancelled":
                raise JobCancelledError("Job cancelled by user")
            _transition(job, status, message, metadata or {})

        previous_project_dir = os.environ.get("PPT_MASTER_CURRENT_PROJECT_DIR")
        os.environ["PPT_MASTER_CURRENT_PROJECT_DIR"] = str(project_dir)
        try:
            brief_payload = json.loads(Path(job.brief_path).read_text(encoding="utf-8"))
            route = str(brief_payload.get("route") or "generate_pptx")
            _transition(job, "routing", f"Routing to PPT Master core: {route}")
            pptx_path = run_core_route(
                route=route,
                brief_path=job.brief_path,
                source_path=job.source_path,
                output_path=output_path,
                project_dir=project_dir,
                keep_project=True,
                max_attempts=max_attempts,
                status_callback=on_pipeline_status,
            )
        finally:
            if previous_project_dir is None:
                os.environ.pop("PPT_MASTER_CURRENT_PROJECT_DIR", None)
            else:
                os.environ["PPT_MASTER_CURRENT_PROJECT_DIR"] = previous_project_dir
        _transition(job, "validating", "Validating generated PPTX", {"pptx_path": str(pptx_path)})
        if not Path(pptx_path).exists():
            raise RuntimeError(f"PPTX output does not exist: {pptx_path}")

        job.metrics = _collect_job_metrics(job)
        _transition(
            job,
            "completed",
            "Job completed",
            {
                "pptx_path": str(pptx_path),
                "generation_report": str(project_dir / "generation_report.json"),
            },
        )
    except JobCancelledError as exc:
        job.error_code = "cancelled"
        job.error_message = str(exc)
        job.failure_analysis = {}
        _transition(job, "cancelled", "Job cancelled by user")
    except Exception as exc:
        job.error_code = exc.__class__.__name__
        job.error_message = str(exc)
        job.failure_analysis = _analyze_failure(job.error_code, job.error_message)
        job.metrics = _collect_job_metrics(job)
        diagnostics = project_dir / "error_traceback.txt"
        diagnostics.write_text(redact_secrets(traceback.format_exc()), encoding="utf-8")
        _transition(
            job,
            "failed",
            "Job failed",
            {"diagnostics": str(diagnostics)},
        )

    return job


def _analyze_failure(error_code: str, error_message: str) -> dict[str, Any]:
    text = f"{error_code}\n{error_message}".lower()
    if "svg_quality_checker" in text or "[scan] checking" in text:
        stage = "quality_check"
        summary = "SVG 质量检查没有通过。"
        suggestions = ["查看 svg_quality_checker.log", "检查 forbidden SVG 属性或元素", "重试后端清洗或手动修复对应 SVG"]
    elif "design_spec" in text or "spec_lock" in text or "strategist" in text:
        stage = "strategist"
        summary = "设计规划阶段没有产出符合契约的 design_spec/spec_lock。"
        suggestions = ["重试任务", "检查 strategist_raw_response.txt", "收紧 Strategist 输出格式或换更稳定模型"]
    elif "no svg files" in text or "svg_output" in text and "missing" in text:
        stage = "svg_generation"
        summary = "没有生成可用于导出的 SVG 页面。"
        suggestions = ["检查大纲是否成功生成", "提高 max_attempts 后重试", "查看 llm_attempts 中模型原始返回"]
    elif "json" in text or "non-whitespace" in text or "unterminated string" in text:
        stage = "llm_response_parse"
        summary = "模型返回内容不是完整的纯 JSON，解析失败。"
        suggestions = ["重试任务", "提高 max_attempts 或换更稳定模型", "查看 llm_attempts 中模型原始返回"]
    elif "llm request failed after" in text or "timed out" in text or "timeout=" in text:
        stage = "llm_timeout"
        summary = "模型请求超时或连接中断。"
        suggestions = ["稍后重试任务", "降低页数或关闭复杂生图/动画", "切换更稳定的模型或提高 LLM_TIMEOUT_SECONDS"]
    elif "total_md_split" in text or "notes" in text:
        stage = "speaker_notes"
        summary = "演讲稿/备注拆分阶段失败。"
        suggestions = ["确认每页都有 notes", "可临时关闭备注或使用占位 notes 后重试"]
    elif "finalize_svg" in text:
        stage = "svg_finalize"
        summary = "SVG 后处理阶段失败。"
        suggestions = ["检查 SVG XML 合法性", "查看 finalize_svg.log", "确认图片路径和资源文件存在"]
    elif "svg_to_pptx" in text:
        stage = "pptx_export"
        summary = "PPTX 导出阶段失败。"
        suggestions = ["查看 svg_to_pptx 错误日志", "确认 svg_final/svg_output 有页面", "检查依赖和字体环境"]
    elif "image_gen" in text or "image" in text and "backend" in text:
        stage = "image_generation"
        summary = "图片生成阶段失败。"
        suggestions = ["检查 IMAGE_BACKEND 和对应 API Key", "先关闭生图生成无图版", "保留 image_prompts.md 后续补图"]
    elif "notes_to_audio" in text or "tts" in text or "audio" in text:
        stage = "audio_generation"
        summary = "旁白音频生成阶段失败。"
        suggestions = ["检查 edge-tts 或云端 TTS 配置", "先关闭音频生成导出 PPTX", "确认 notes 目录已有每页讲稿"]
    else:
        stage = "pipeline"
        summary = "生成流程中断。"
        suggestions = ["查看 error_traceback.txt", "用更短页数重试", "检查模型/API 是否可用"]
    return {
        "stage": stage,
        "summary": summary,
        "suggestions": suggestions,
    }


def load_job(status_path: str | Path) -> JobStatus:
    data = json.loads(Path(status_path).read_text(encoding="utf-8"))
    events = [JobEvent(**event) for event in data.pop("events", [])]
    return JobStatus(**data, events=events)


def main() -> None:
    parser = argparse.ArgumentParser(description="PPT Master local worker job runner")
    parser.add_argument("--brief", required=True, help="Path to brief.json")
    parser.add_argument("--source", required=True, help="Path to source Markdown file")
    parser.add_argument("--jobs-dir", default="/tmp/ppt-master-jobs", help="Directory for job workspaces")
    parser.add_argument("--job-id", default=None, help="Optional deterministic job id")
    parser.add_argument("--max-attempts", type=int, default=2, help="Max LLM attempts per slide")
    parser.add_argument("--status-only", action="store_true", help="Create queued job without running it")

    args = parser.parse_args()
    job = create_job(
        brief_path=args.brief,
        source_path=args.source,
        jobs_dir=args.jobs_dir,
        job_id=args.job_id,
    )

    if not args.status_only:
        job = run_job(job, max_attempts=args.max_attempts)

    status_path = Path(job.project_dir) / "job_status.json"
    print(json.dumps({
        "job_id": job.job_id,
        "status": job.status,
        "status_path": str(status_path),
        "output_path": job.output_path,
        "error_code": job.error_code,
        "error_message": job.error_message,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
