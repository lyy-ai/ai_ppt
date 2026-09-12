#!/usr/bin/env python3
"""End-to-end orchestrator: Brief -> Strategist -> Executor -> PPTX.

Usage:
    python -m skills.ppt-master.scripts.cloud_generator.orchestrator \
        --brief /path/to/brief.json \
        --source /path/to/source.md \
        --output /path/to/output.pptx \
        [--project-dir /path/to/project]

The orchestrator:
1. Reads the Brief JSON
2. Runs Strategist to produce design_spec.md + spec_lock.md
3. Runs Executor to produce svg_output/*.svg + notes/*.md
4. Runs svg_to_pptx.py to produce the final PPTX
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .security import redact_secrets

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
TEMPLATES_DIR = SKILL_DIR / "templates"
StatusCallback = Callable[[str, str, Optional[dict]], None]


def _find_python() -> str:
    """Find a Python 3.10+ interpreter with required dependencies."""
    candidates = [
        sys.executable,
        "/tmp/ppt-master-venv-312/bin/python",
        "/tmp/ppt-master-venv/bin/python",
    ]
    for candidate in candidates:
        p = Path(candidate)
        if p.exists():
            return str(p)
    return sys.executable


def _run_strategist(brief_path: Path, source_path: Path, project_dir: Path) -> dict:
    """Run the Strategist phase."""
    from .brief_schema import Brief
    from .strategist import run_strategist

    brief = Brief.from_file(brief_path)
    errors = brief.validate()
    if errors:
        raise ValueError(f"Brief validation failed: {errors}")

    return run_strategist(brief, source_path, project_dir)


def _load_brief(brief_path: Path):
    from .brief_schema import Brief

    brief = Brief.from_file(brief_path)
    errors = brief.validate()
    if errors:
        raise ValueError(f"Brief validation failed: {errors}")
    return brief


def _run_executor(
    project_dir: Path,
    source_path: Path,
    include_notes: bool,
    max_attempts: int,
    status_callback=None,
) -> dict:
    """Run the Executor phase."""
    from .executor import run_executor
    return run_executor(
        project_dir,
        source_path,
        include_notes=include_notes,
        max_attempts=max_attempts,
        status_callback=status_callback,
    )


def _run_script(project_dir: Path, script_name: str, *args: str, timeout: int = 300) -> str:
    python = _find_python()
    script = SCRIPTS_DIR / script_name
    cmd = [python, str(script), str(project_dir), *args]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    log_name = script_name.replace(".py", "")
    log_path = project_dir / f"{log_name}.log"
    log_path.write_text(
        "\n".join([
            f"$ {' '.join(cmd)}",
            "",
            "STDOUT:",
            redact_secrets(result.stdout),
            "",
            "STDERR:",
            redact_secrets(result.stderr),
        ]),
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(redact_secrets(f"{script_name} failed:\n{result.stderr}\n{result.stdout}"))
    return str(log_path)


def _resolve_template_source(brief) -> Path | None:
    raw_path = str(getattr(brief, "template_path", "") or "").strip()
    template_id = str(getattr(brief, "template_id", "") or "").strip()
    template_kind = str(getattr(brief, "template_kind", "") or "").strip()

    candidates: list[Path] = []
    if raw_path:
        path = Path(raw_path).expanduser()
        candidates.append(path if path.is_absolute() else (Path.cwd() / path))
        candidates.append(TEMPLATES_DIR / raw_path)

    if template_id:
        if template_kind:
            candidates.append(TEMPLATES_DIR / f"{template_kind}s" / template_id)
        for kind in ("brands", "layouts", "decks"):
            candidates.append(TEMPLATES_DIR / kind / template_id)

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            continue
        if resolved.exists() and resolved.is_dir():
            return resolved
    return None


def _prepare_template_assets(project_dir: Path, brief, emit: StatusCallback | None = None) -> Path | None:
    source = _resolve_template_source(brief)
    if not source:
        if getattr(brief, "template_id", "") or getattr(brief, "template_path", ""):
            raise ValueError(f"Template not found: {getattr(brief, 'template_id', '') or getattr(brief, 'template_path', '')}")
        return None

    target = project_dir / "templates"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)

    metadata = {
        "source": str(source),
        "kind": getattr(brief, "template_kind", "") or source.parent.name.removesuffix("s"),
        "id": getattr(brief, "template_id", "") or source.name,
    }
    (target / "template_selection.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    if emit:
        emit("template", "Template assets copied into project", metadata)
    return target


def _run_quality_check(project_dir: Path) -> str:
    """Run PPT Master's mandatory SVG quality gate against svg_output/."""
    return _run_script(project_dir, "svg_quality_checker.py", timeout=180)


def _run_notes_split(project_dir: Path) -> str | None:
    """Run speaker-note splitting when notes/total.md exists."""
    if not (project_dir / "notes" / "total.md").exists():
        return None
    return _run_script(project_dir, "total_md_split.py", timeout=120)


def _run_notes_audio(project_dir: Path, provider: str = "edge", voice: str = "") -> str:
    if os.environ.get("CLOUD_GENERATOR_MOCK") == "1":
        audio_dir = project_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        notes_dir = project_dir / "notes"
        generated = 0
        for note_path in sorted(notes_dir.glob("*.md")):
            if note_path.name == "total.md":
                continue
            out_path = audio_dir / f"{note_path.stem}.wav"
            _write_mock_wav(out_path)
            generated += 1
        log_path = project_dir / "notes_to_audio.log"
        log_path.write_text(f"CLOUD_GENERATOR_MOCK=1 generated {generated} placeholder WAV files\\n", encoding="utf-8")
        return str(log_path)
    python = _find_python()
    script = SCRIPTS_DIR / "notes_to_audio.py"
    cmd = [python, str(script), str(project_dir), "--provider", provider or "edge"]
    if voice:
        if provider == "edge":
            cmd.extend(["--voice", voice])
        else:
            cmd.extend(["--voice-id", voice])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    log_path = project_dir / "notes_to_audio.log"
    log_path.write_text(
        "\n".join([
            f"$ {' '.join(cmd)}",
            "",
            "STDOUT:",
            redact_secrets(result.stdout),
            "",
            "STDERR:",
            redact_secrets(result.stderr),
        ]),
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(redact_secrets(f"notes_to_audio.py failed:\n{result.stderr}\n{result.stdout}"))
    return str(log_path)


def _write_mock_wav(path: Path) -> None:
    sample_rate = 8000
    frame_count = max(1, sample_rate // 10)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00\x00" * frame_count)


def _run_finalize_svg(project_dir: Path) -> str:
    """Run PPT Master's SVG post-processing pipeline into svg_final/."""
    return _run_script(project_dir, "finalize_svg.py", "--quiet", timeout=240)


def _run_animation_scaffold(project_dir: Path) -> str:
    python = _find_python()
    script = SCRIPTS_DIR / "animation_config.py"
    cmd = [python, str(script), "scaffold", str(project_dir), "--force"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    log_path = project_dir / "animation_config.log"
    log_path.write_text(
        "\n".join([
            f"$ {' '.join(cmd)}",
            "",
            "STDOUT:",
            redact_secrets(result.stdout),
            "",
            "STDERR:",
            redact_secrets(result.stderr),
        ]),
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(redact_secrets(f"animation_config.py failed:\n{result.stderr}\n{result.stdout}"))
    return str(log_path)


def _snapshot_svg_output(project_dir: Path) -> Path | None:
    svg_dir = project_dir / "svg_output"
    if not svg_dir.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_root = project_dir / "backup" / stamp
    for index in range(2, 1000):
        if not backup_root.exists():
            break
        backup_root = project_dir / "backup" / f"{stamp}_{index}"
    backup_dir = backup_root / "svg_output"
    shutil.copytree(svg_dir, backup_dir)
    return backup_dir


def _run_svg_to_pptx(project_dir: Path, output_path: Path, brief=None, *, include_narration: bool = False) -> Path:
    """Run the SVG-to-PPTX conversion."""
    python = _find_python()
    svg_to_pptx = SCRIPTS_DIR / "svg_to_pptx.py"

    cmd = [
        python, str(svg_to_pptx),
        str(project_dir),
        "-o", str(output_path),
    ]
    # The upstream v4.2 exporter always writes the native editable PPTX. The
    # old cloud-only --svg-snapshot/--only/--workers flags were removed from
    # the core CLI; SVG preview remains available from the project artifacts.
    if bool(getattr(brief, "merge_paragraphs", False)):
        cmd.append("--merge-paragraphs")
    include_animations = bool(getattr(brief, "include_animations", False))
    if include_narration and not include_animations:
        cmd.append("--no-animations")
    elif include_animations:
        cmd.extend(["--animation", "auto", "--animation-trigger", "after-previous"])
    else:
        cmd.extend(["--animation", "none", "--transition", "none"])
    audio_dir = project_dir / "audio"
    if include_narration:
        cmd.extend(["--recorded-narration", str(audio_dir), "--use-narration-timings"])
        # Upstream v4.2 treats recorded narration as an animation-config
        # consumer unless no-animations is explicit. The cloud default keeps
        # narration audio and slide timings while disabling object animation;
        # animated narration uses the canonical config explicitly until the
        # semantic narration_timing stage is available in the cloud adapter.
        if include_animations:
            cmd.extend(["--animation-config", str(project_dir / "animations.json")])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(redact_secrets(f"svg_to_pptx failed:\n{result.stderr}\n{result.stdout}"))

    return output_path


def run_pipeline(
    brief_path: str | Path,
    source_path: str | Path,
    output_path: str | Path,
    project_dir: str | Path | None = None,
    keep_project: bool = False,
    max_attempts: int = 2,
    status_callback: StatusCallback | None = None,
) -> Path:
    """Run the full pipeline: Brief -> Strategist -> Executor -> PPTX.

    Returns the path to the generated PPTX file.
    """
    brief_path = Path(brief_path)
    source_path = Path(source_path)
    output_path = Path(output_path)

    if project_dir is None:
        project_dir = Path(tempfile.mkdtemp(prefix="ppt-master-task-"))
    else:
        project_dir = Path(project_dir)

    project_dir.mkdir(parents=True, exist_ok=True)
    svg_dir = project_dir / "svg_output"
    notes_dir = project_dir / "notes"
    svg_dir.mkdir(parents=True, exist_ok=True)
    notes_dir.mkdir(parents=True, exist_ok=True)

    # Copy source into project
    dest_source = project_dir / "sources" / source_path.name
    dest_source.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, dest_source)

    def emit(status: str, message: str, metadata: dict | None = None) -> None:
        if status_callback:
            status_callback(status, message, metadata)

    brief = _load_brief(brief_path)

    print(f"[1/8] Template: preparing selected template assets ...")
    template_target = _prepare_template_assets(project_dir, brief, emit=emit)
    if template_target:
        print(f"  -> {template_target}")
    else:
        print("  -> free design")

    print(f"[2/8] Strategist: generating design_spec.md + spec_lock.md ...")
    emit("strategist", "Generating design_spec.md and spec_lock.md")
    strat_result = _run_strategist(brief_path, dest_source, project_dir)
    print(f"  -> {strat_result['design_spec']}")
    print(f"  -> {strat_result['spec_lock']}")

    if bool(getattr(brief, "include_images", False)):
        print(f"[3/8] Images: preparing AI image manifest ...")
        emit("image_manifest", "Preparing AI image prompt manifest")
        from .image_manifest import maybe_prepare_ai_images

        image_result = maybe_prepare_ai_images(project_dir, brief, status_callback=emit)
        if image_result.get("manifest"):
            print(f"  -> {image_result['manifest']}")
        if image_result.get("markdown"):
            print(f"  -> {image_result['markdown']}")
    else:
        print(f"[3/8] Images: skipped")

    print(f"[4/8] Executor: generating SVG pages and notes ...")
    emit("generating", "Generating SVG pages and speaker notes")
    exec_result = _run_executor(
        project_dir,
        dest_source,
        include_notes=True,
        max_attempts=max_attempts,
        status_callback=emit,
    )
    print(f"  -> {exec_result['page_count']} pages generated in {exec_result['svg_dir']}")
    print(f"  -> report: {exec_result['report']}")

    if bool(getattr(brief, "include_animations", False)):
        print(f"[5/8] Animations: scaffolding animations.json ...")
        emit("animation_config", "Preparing PowerPoint animation configuration")
        animation_log = _run_animation_scaffold(project_dir)
        print(f"  -> {animation_log}")
    else:
        print(f"[5/8] Animations: skipped")

    print(f"[6/8] Quality: checking svg_output ...")
    emit("quality_check", "Running svg_quality_checker.py")
    quality_log = _run_quality_check(project_dir)
    print(f"  -> {quality_log}")

    print(f"[7/8] Notes: splitting speaker notes ...")
    emit("postprocessing", "Splitting speaker notes")
    notes_log = _run_notes_split(project_dir)
    if notes_log:
        print(f"  -> {notes_log}")
    else:
        print("  -> skipped: notes/total.md not found")

    print(f"[8/8] Finalize: post-processing SVG assets ...")
    emit("postprocessing", "Running finalize_svg.py")
    finalize_log = _run_finalize_svg(project_dir)
    print(f"  -> {finalize_log}")

    backup_dir = _snapshot_svg_output(project_dir)
    if backup_dir:
        emit("backup", "Snapshotting svg_output for re-export", {"backup_dir": str(backup_dir)})
        print(f"  -> backup: {backup_dir}")

    print(f"[export] Export: converting SVG to PPTX ...")
    emit("exporting", "Converting SVG to native editable PPTX")
    pptx_path = _run_svg_to_pptx(project_dir, output_path, brief=brief)
    print(f"  -> {pptx_path}")

    if bool(getattr(brief, "include_audio", False)):
        print(f"[audio] Audio: generating narration files from notes ...")
        emit("audio_generation", "Generating narration audio from speaker notes")
        audio_log = _run_notes_audio(
            project_dir,
            provider=str(getattr(brief, "audio_provider", "edge") or "edge"),
            voice=str(getattr(brief, "audio_voice", "") or ""),
        )
        print(f"  -> {audio_log}")

        narrated_path = output_path.with_name(f"{output_path.stem}_narrated{output_path.suffix}")
        print(f"[export] Export: converting SVG to narrated PPTX ...")
        emit("exporting", "Converting SVG to narrated PPTX")
        narrated_pptx_path = _run_svg_to_pptx(project_dir, narrated_path, brief=brief, include_narration=True)
        print(f"  -> {narrated_pptx_path}")

    if not keep_project and project_dir != Path(tempfile.gettempdir()):
        # Keep project by default for debugging; only clean if explicitly asked
        pass

    return pptx_path


def main() -> None:
    parser = argparse.ArgumentParser(description="PPT Master Cloud Generator")
    parser.add_argument("--brief", required=True, help="Path to brief.json")
    parser.add_argument("--source", required=True, help="Path to source Markdown file")
    parser.add_argument("--output", required=True, help="Path for output PPTX")
    parser.add_argument("--project-dir", default=None, help="Project directory (temp if omitted)")
    parser.add_argument("--keep", action="store_true", help="Keep project directory after run")
    parser.add_argument("--max-attempts", type=int, default=2, help="Max LLM attempts per slide")

    args = parser.parse_args()

    try:
        pptx_path = run_pipeline(
            brief_path=args.brief,
            source_path=args.source,
            output_path=args.output,
            project_dir=args.project_dir,
            keep_project=args.keep,
            max_attempts=args.max_attempts,
        )
        print(f"\nDone: {pptx_path}")
    except Exception as exc:
        print(f"Pipeline failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
