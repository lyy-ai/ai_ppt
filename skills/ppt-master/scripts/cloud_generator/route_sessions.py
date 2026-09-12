"""Staged sessions for native PPT Master routes.

Native routes are not one-shot Generate jobs.  They require an inspected source
artifact and an explicit confirmation before mutation.  This module provides a
small JSON-backed session boundary for the cloud adapter while keeping the
upstream CLI tools as the implementation authority.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .security import redact_secrets
from .source_materials import materialize_source


ROUTES = {"create_template", "fill_native_pptx", "enhance_native_pptx"}


class RouteSessionError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RouteSessionError(f"Invalid route session state: {path}") from exc
    if not isinstance(data, dict):
        raise RouteSessionError(f"Route session state must be an object: {path}")
    return data


def _run(command: list[str], *, cwd: Path | None = None, timeout: int = 600) -> str:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    output = redact_secrets("\n".join([f"$ {' '.join(command)}", result.stdout, result.stderr]))
    if result.returncode != 0:
        raise RouteSessionError(output.strip() or f"Command failed: {command[0]}")
    return output


def _script(skill_dir: Path, name: str) -> Path:
    path = skill_dir / "scripts" / name
    if not path.exists():
        raise RouteSessionError(f"PPT Master core script is missing: {name}")
    return path


def _source_pptx(session_dir: Path) -> Path:
    candidates = sorted((session_dir / "input" / "materials").glob("*.pptx"))
    if len(candidates) != 1:
        raise RouteSessionError("Native routes require exactly one uploaded .pptx source")
    return candidates[0]


def _state_path(session_dir: Path) -> Path:
    return session_dir / "route_session.json"


def _postgres_state_store_enabled() -> bool:
    return str(os.environ.get("PPT_MASTER_ROUTE_SESSION_STORE") or "").strip().lower() in {"postgres", "postgresql"}


def _persist_state(jobs_dir: Path, state: dict[str, Any]) -> None:
    """Persist state locally and, when configured, in the durable Postgres store."""
    session_dir = Path(str(state.get("session_dir") or jobs_dir / "_route_sessions" / state["session_id"]))
    _write_json(_state_path(session_dir), state)
    if not _postgres_state_store_enabled():
        return
    try:
        from .postgres_queue import connect
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO route_sessions (id, owner_user_id, route, status, state_json, created_at, updated_at)
                VALUES (%(id)s, %(owner)s, %(route)s, %(status)s, %(state)s::jsonb, %(created)s, %(updated)s)
                ON CONFLICT (id) DO UPDATE SET
                  owner_user_id = EXCLUDED.owner_user_id,
                  route = EXCLUDED.route,
                  status = EXCLUDED.status,
                  state_json = EXCLUDED.state_json,
                  updated_at = EXCLUDED.updated_at
                """,
                {
                    "id": state["session_id"],
                    "owner": str(state.get("owner_user_id") or ""),
                    "route": str(state.get("route") or ""),
                    "status": str(state.get("status") or ""),
                    "state": json.dumps(state, ensure_ascii=False),
                    "created": state.get("created_at"),
                    "updated": state.get("updated_at"),
                },
            )
            conn.commit()
    except ImportError as exc:
        raise RouteSessionError("Postgres route-session storage requires psycopg") from exc


def _set_progress(jobs_dir: Path, state: dict[str, Any], *, stage: str, percent: int, message: str) -> None:
    """Persist a small progress event while a native route is preparing."""
    state["progress"] = {
        "stage": str(stage),
        "percent": max(0, min(100, int(percent))),
        "message": str(message)[:240],
        "at": _now(),
    }
    state["updated_at"] = _now()
    _persist_state(jobs_dir, state)


def _load_persisted_state(jobs_dir: Path, session_id: str) -> dict[str, Any]:
    if _postgres_state_store_enabled():
        try:
            from .postgres_queue import connect
            with connect() as conn:
                row = conn.execute("SELECT state_json FROM route_sessions WHERE id = %s", (session_id,)).fetchone()
            if row:
                value = row["state_json"] if isinstance(row, dict) else row[0]
                if isinstance(value, str):
                    value = json.loads(value)
                if isinstance(value, dict):
                    return value
        except ImportError as exc:
            raise RouteSessionError("Postgres route-session storage requires psycopg") from exc
    local_state = _read_json(_state_path(jobs_dir / "_route_sessions" / session_id))
    if _postgres_state_store_enabled():
        _persist_state(jobs_dir, local_state)
    return local_state


def _recover_interrupted_state(jobs_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the last safe state after an API process dies mid-preparation."""
    if state.get("status") != "preparing":
        return state
    session_dir = Path(str(state.get("session_dir") or ""))
    route = str(state.get("route") or "")
    artifacts = dict(state.get("artifacts") or {})
    recovered = False
    if route == "create_template":
        workspace = session_dir / "template_workspace"
        if (workspace / "manifest.json").is_file() and (workspace / "native_structure.json").is_file():
            artifacts.update({
                "workspace": str(workspace),
                "manifest": str(workspace / "manifest.json"),
                "native_structure": str(workspace / "native_structure.json"),
                "authoring_svg": str(workspace / "authoring-svg"),
                "authoring_summary": str(workspace / "authoring-svg" / "authoring_summary.json"),
            })
            state.update({"status": "awaiting_authoring", "artifacts": artifacts, "recovered_at": _now(), "progress": {"stage": "awaiting_authoring", "percent": 100, "message": "Authoring review is ready after recovery", "at": _now()}})
            recovered = True
    elif route == "fill_native_pptx":
        library = next(iter((session_dir / "analysis").glob("*.slide_library.json")), None)
        plan = session_dir / "analysis" / "fill_plan.json"
        if library and plan.is_file():
            artifacts.update({"slide_library": str(library), "fill_plan": str(plan)})
            state.update({"status": "awaiting_confirmation", "artifacts": artifacts, "confirmation": {"fill_plan": _read_json(plan)}, "recovered_at": _now(), "progress": {"stage": "awaiting_confirmation", "percent": 100, "message": "Fill plan is ready after recovery", "at": _now()}})
            recovered = True
    elif route == "enhance_native_pptx":
        project_dir = session_dir / "native_enhance"
        plan = project_dir / "analysis" / "enhancement_plan.json"
        if plan.is_file():
            artifacts.update({"project": str(project_dir), "plan": str(plan)})
            state.update({"status": "awaiting_confirmation", "artifacts": artifacts, "confirmation": {"enhancement_plan": _read_json(plan)}, "recovered_at": _now(), "progress": {"stage": "awaiting_confirmation", "percent": 100, "message": "Enhancement plan is ready after recovery", "at": _now()}})
            recovered = True
    if not recovered:
        state.update({"status": "failed", "error": "Route session preparation was interrupted; create a new session.", "recovered_at": _now(), "progress": {"stage": "failed", "percent": 100, "message": "Preparation was interrupted", "at": _now()}})
    state["updated_at"] = _now()
    _persist_state(jobs_dir, state)
    return state


def update_route_session(jobs_dir: Path, session_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    """Apply a small atomic state update used by the API billing boundary."""
    state = load_route_session(jobs_dir, session_id)
    state.update(updates)
    state["updated_at"] = _now()
    _persist_state(jobs_dir, state)
    return state


def _copy_native_assets(session_dir: Path, project_dir: Path) -> None:
    materials = session_dir / "input" / "materials"
    notes_dir = project_dir / "notes"
    audio_dir = project_dir / "audio"
    notes_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)
    for path in materials.iterdir():
        if path.suffix.lower() in {".md", ".markdown", ".txt"}:
            shutil.copy2(path, notes_dir / f"{path.stem}.md")
        elif path.suffix.lower() in {".mp3", ".wav", ".m4a"}:
            shutil.copy2(path, audio_dir / path.name)


def _write_consumable_design_spec(package_dir: Path, metadata: dict[str, Any]) -> Path:
    """Materialize the imported manifest into the core template contract."""
    manifest_path = package_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RouteSessionError("Published template manifest is missing or invalid") from exc
    if not isinstance(manifest, dict):
        raise RouteSessionError("Published template manifest must be an object")
    canvas = manifest.get("canvas") if isinstance(manifest.get("canvas"), dict) else {}
    theme = manifest.get("theme") if isinstance(manifest.get("theme"), dict) else {}
    slides = manifest.get("slides") if isinstance(manifest.get("slides"), list) else []
    fonts = manifest.get("fonts") if isinstance(manifest.get("fonts"), list) else []
    authoring = metadata.get("authoring") if isinstance(metadata.get("authoring"), dict) else {}
    requested_color = str(authoring.get("primary_color") or "").strip()
    primary = requested_color if re.fullmatch(r"#[0-9A-Fa-f]{6}", requested_color) else str(theme.get("primaryColor") or theme.get("primary_color") or "#1E293B")
    font_family = str(authoring.get("font_family") or (fonts[0].get("name") if fonts and isinstance(fonts[0], dict) else "Inter") or "Inter").strip()[:120]
    width = canvas.get("width") or canvas.get("cx") or 13.333
    height = canvas.get("height") or canvas.get("cy") or 7.5
    design_spec = f"""---
deck_id: {metadata['template_id']}
kind: deck
summary: {metadata['name']}
primary_color: {primary}
canvas_format: ppt169
canvas_width: {width}
canvas_height: {height}
replication_mode: mirror
native_structure_mode: structured
page_count: {len(slides)}
---

# Template Overview

This reusable template was imported from an editable PowerPoint source and
published through PPT Master. Preserve its recurring visual system while
rewriting content for the requested audience.

## Authoring Contract

- Preserve the imported slide geometry, hierarchy, and source relationships.
- Keep the primary identity color `{primary}` unless the user explicitly asks
  for a brand change.
- Use the imported Master/Layout structure and editable text roles.
- Prefer the imported font family when it is available in the runtime.
- Preferred font family: `{font_family}`.

## Imported Evidence

- Source slides: {len(slides)}
- Imported font records: {len(fonts)}
- Source canvas: {width} x {height}
"""
    path = package_dir / "design_spec.md"
    path.write_text(design_spec, encoding="utf-8")
    return path


def prepare_route_session(
    *,
    jobs_dir: Path,
    skill_dir: Path,
    payload: dict[str, Any],
    owner_user_id: str = "",
) -> dict[str, Any]:
    route = str(payload.get("route") or "").strip()
    if route not in ROUTES:
        raise RouteSessionError(f"Unsupported native route: {route}")
    session_id = f"route_{uuid.uuid4().hex[:12]}"
    session_dir = jobs_dir / "_route_sessions" / session_id
    input_dir = session_dir / "input"
    source_text = str(payload.get("source_text") or "")
    materials = payload.get("material_files") or []
    if not isinstance(materials, list):
        raise RouteSessionError("material_files must be a list")
    materialize_source(input_dir=input_dir, source_text=source_text, materials=materials, source_urls=[])
    source_pptx = _source_pptx(session_dir)
    state: dict[str, Any] = {
        "schema": "ppt_master_route_session.v1",
        "session_id": session_id,
        "route": route,
        "status": "preparing",
        "owner_user_id": owner_user_id,
        "created_at": _now(),
        "updated_at": _now(),
        "session_dir": str(session_dir),
        "source_pptx": str(source_pptx),
        "artifacts": {},
        "progress": {"stage": "preparing", "percent": 5, "message": "Preparing native route", "at": _now()},
    }
    _persist_state(jobs_dir, state)

    if route == "create_template":
        workspace = session_dir / "template_workspace"
        _set_progress(jobs_dir, state, stage="importing", percent=25, message="Importing editable PowerPoint structure")
        log = _run([
            sys.executable,
            str(_script(skill_dir, "pptx_template_import.py")),
            str(source_pptx),
            "-o",
            str(workspace),
            "--inheritance-mode",
            "both",
        ])
        _set_progress(jobs_dir, state, stage="projecting", percent=70, message="Building the editable authoring view")
        _run([
            sys.executable,
            str(_script(skill_dir, "svg_authoring_view.py")),
            str(workspace / "svg"),
            "-o",
            str(workspace / "authoring-svg"),
            "--projection-kind",
            "layered",
        ])
        state["status"] = "awaiting_authoring"
        state["artifacts"] = {
            "workspace": str(workspace),
            "manifest": str(workspace / "manifest.json"),
            "native_structure": str(workspace / "native_structure.json"),
            "authoring_svg": str(workspace / "authoring-svg"),
            "authoring_summary": str(workspace / "authoring-svg" / "authoring_summary.json"),
            "log": str(session_dir / "template_import.log"),
        }
        (session_dir / "template_import.log").write_text(log, encoding="utf-8")
        _set_progress(jobs_dir, state, stage="awaiting_authoring", percent=100, message="Authoring review is ready")
    elif route == "fill_native_pptx":
        analysis_dir = session_dir / "analysis"
        library = analysis_dir / f"{source_pptx.stem}.slide_library.json"
        plan = analysis_dir / "fill_plan.json"
        _set_progress(jobs_dir, state, stage="analyzing", percent=40, message="Analyzing native slide structure")
        _run([sys.executable, str(_script(skill_dir, "template_fill_pptx.py")), "analyze", str(source_pptx), "-o", str(library)])
        _set_progress(jobs_dir, state, stage="planning", percent=80, message="Preparing a fill plan for review")
        _run([sys.executable, str(_script(skill_dir, "template_fill_pptx.py")), "scaffold", str(library), "-o", str(plan)])
        state["status"] = "awaiting_confirmation"
        state["artifacts"] = {"slide_library": str(library), "fill_plan": str(plan)}
        state["confirmation"] = {"fill_plan": _read_json(plan)}
        _set_progress(jobs_dir, state, stage="awaiting_confirmation", percent=100, message="Fill plan is ready for review")
    else:
        project_dir = session_dir / "native_enhance"
        _set_progress(jobs_dir, state, stage="initializing", percent=35, message="Initializing native enhancement workspace")
        _run([
            sys.executable,
            str(_script(skill_dir, "native_enhance_pptx.py")),
            "init",
            str(source_pptx),
            "--project-dir",
            str(project_dir),
        ])
        _copy_native_assets(session_dir, project_dir)
        _set_progress(jobs_dir, state, stage="planning", percent=80, message="Preparing enhancement modules for review")
        _run([sys.executable, str(_script(skill_dir, "native_enhance_pptx.py")), "plan", str(project_dir)])
        state["status"] = "awaiting_confirmation"
        state["artifacts"] = {"project": str(project_dir), "plan": str(project_dir / "analysis" / "enhancement_plan.json")}
        state["confirmation"] = {"enhancement_plan": _read_json(project_dir / "analysis" / "enhancement_plan.json")}
        _set_progress(jobs_dir, state, stage="awaiting_confirmation", percent=100, message="Enhancement plan is ready for review")

    state["updated_at"] = _now()
    _persist_state(jobs_dir, state)
    return state


def load_route_session(jobs_dir: Path, session_id: str) -> dict[str, Any]:
    safe_id = Path(session_id).name
    if safe_id != session_id:
        raise RouteSessionError("Invalid route session id")
    return _recover_interrupted_state(jobs_dir, _load_persisted_state(jobs_dir, safe_id))


def confirm_route_session(*, jobs_dir: Path, skill_dir: Path, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    state = load_route_session(jobs_dir, session_id)
    confirmation_key = str(payload.get("idempotency_key") or f"confirm:{session_id}").strip()
    if state.get("status") == "completed":
        if confirmation_key == str(state.get("confirmation_key") or f"confirm:{session_id}"):
            return state
        raise RouteSessionError("Route session has already been confirmed")
    if state.get("status") != "awaiting_confirmation":
        raise RouteSessionError(f"Route session is not awaiting confirmation: {state.get('status')}")
    session_dir = Path(state["session_dir"])
    route = state["route"]
    if payload.get("confirmed") is not True:
        raise RouteSessionError("Explicit confirmed=true is required before applying a native route")

    state["confirmation_key"] = confirmation_key
    state["confirmation_received_at"] = _now()
    _set_progress(jobs_dir, state, stage="applying", percent=50, message="Applying the confirmed native workflow")

    if route == "fill_native_pptx":
        plan_path = Path(state["artifacts"]["fill_plan"])
        plan = payload.get("fill_plan")
        if not isinstance(plan, dict):
            raise RouteSessionError("fill_plan object is required")
        plan = dict(plan)
        plan["status"] = "confirmed"
        _write_json(plan_path, plan)
        library = Path(state["artifacts"]["slide_library"])
        report = session_dir / "analysis" / "check_report.json"
        _run([sys.executable, str(_script(skill_dir, "template_fill_pptx.py")), "check-plan", str(library), str(plan_path), "-o", str(report)])
        output_dir = session_dir / "exports"
        output_dir.mkdir(parents=True, exist_ok=True)
        requested_output = output_dir / "filled.pptx"
        before = set(output_dir.glob("*.pptx"))
        _run([sys.executable, str(_script(skill_dir, "template_fill_pptx.py")), "apply", str(state["source_pptx"]), str(plan_path), "-o", str(requested_output)])
        generated = sorted(set(output_dir.glob("*.pptx")) - before, key=lambda path: path.stat().st_mtime)
        if len(generated) != 1:
            raise RouteSessionError("Template-fill CLI did not produce exactly one PPTX export")
        output = generated[0]
        state["artifacts"].update({"check_report": str(report), "output": str(output)})
    elif route == "enhance_native_pptx":
        project_dir = Path(state["artifacts"]["project"])
        plan = payload.get("enhancement_plan")
        if not isinstance(plan, dict):
            raise RouteSessionError("enhancement_plan object is required")
        plan = dict(plan)
        plan["status"] = "confirmed"
        _write_json(Path(state["artifacts"]["plan"]), plan)
        output = session_dir / "exports" / "enhanced.pptx"
        _run([sys.executable, str(_script(skill_dir, "native_enhance_pptx.py")), "apply", str(project_dir), "-o", str(output)])
        _run([sys.executable, str(_script(skill_dir, "native_enhance_pptx.py")), "validate", str(project_dir)])
        state["artifacts"]["output"] = str(output)
    else:
        raise RouteSessionError(f"Route does not support confirmation: {route}")

    state["status"] = "completed"
    _set_progress(jobs_dir, state, stage="completed", percent=100, message="Native PPTX export is ready")
    return state


def publish_template_session(*, jobs_dir: Path, skill_dir: Path, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Publish a prepared Create Template workspace as a durable local package."""
    state = load_route_session(jobs_dir, session_id)
    if state.get("route") != "create_template":
        raise RouteSessionError("Only Create Template sessions can be published")
    if state.get("status") not in {"awaiting_authoring", "published"}:
        raise RouteSessionError(f"Template session is not publishable: {state.get('status')}")
    publish_key = str(payload.get("idempotency_key") or f"publish:{session_id}").strip()
    if state.get("status") == "published":
        if publish_key == str(state.get("publish_key") or f"publish:{session_id}"):
            return state
        raise RouteSessionError("Template session has already been published")

    workspace = Path(str((state.get("artifacts") or {}).get("workspace") or ""))
    if not workspace.is_dir():
        raise RouteSessionError("Template authoring workspace is missing")
    name = str(payload.get("name") or "").strip()[:120]
    if not name:
        raise RouteSessionError("Template name is required")
    description = str(payload.get("description") or "").strip()[:1000]
    raw_authoring = payload.get("authoring") if isinstance(payload.get("authoring"), dict) else {}
    raw_tags = raw_authoring.get("tags") if isinstance(raw_authoring.get("tags"), list) else []
    authoring = {
        "primary_color": str(raw_authoring.get("primary_color") or "").strip()[:7],
        "font_family": str(raw_authoring.get("font_family") or "").strip()[:120],
        "tags": [str(tag).strip()[:40] for tag in raw_tags[:12] if str(tag).strip()],
    }
    template_id = f"template_{uuid.uuid4().hex[:12]}"
    package_dir = jobs_dir / "_templates" / template_id
    package_dir.parent.mkdir(parents=True, exist_ok=True)
    _run([
        sys.executable,
        str(_script(skill_dir, "mirror_template_materialize.py")),
        str(workspace),
        str(package_dir),
    ])
    for filename in ("manifest.json", "native_structure.json", "source_template.pptx", "conversion-report.json"):
        source = workspace / filename
        if source.is_file():
            shutil.copy2(source, package_dir / filename)
    authoring_source = workspace / "authoring-svg"
    if authoring_source.is_dir():
        shutil.copytree(authoring_source, package_dir / "authoring-svg")
    metadata = {
        "schema": "ppt_master_template.v1",
        "template_id": template_id,
        "name": name,
        "description": description,
        "authoring": authoring,
        "source_session_id": session_id,
        "owner_user_id": state.get("owner_user_id") or "",
        "created_at": _now(),
        "package_dir": str(package_dir),
    }
    _write_json(package_dir / "template.json", metadata)
    _write_consumable_design_spec(package_dir, metadata)
    state.update({
        "status": "published",
        "publish_key": publish_key,
        "published_at": metadata["created_at"],
        "template": {
            "template_id": template_id,
            "name": name,
            "description": description,
        },
        "artifacts": {
            **(state.get("artifacts") or {}),
            "package": str(package_dir),
            "metadata": str(package_dir / "template.json"),
            "design_spec": str(package_dir / "design_spec.md"),
        },
        "updated_at": _now(),
    })
    _set_progress(jobs_dir, state, stage="published", percent=100, message="Reusable template package is published")
    return state
