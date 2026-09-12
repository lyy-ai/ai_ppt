#!/usr/bin/env python3
"""AI image manifest support for the cloud generator pipeline."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import struct
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .security import redact_secrets


SCRIPTS_DIR = Path(__file__).resolve().parent.parent


def maybe_prepare_ai_images(project_dir: Path, brief: Any, status_callback=None) -> dict[str, str | None]:
    """Create image_prompts.json/md and optionally run image_gen.py.

    This mirrors PPT Master's manifest-first image workflow. If IMAGE_BACKEND is
    configured, the function attempts Path A (`image_gen.py --manifest`). If not,
    it still writes `image_prompts.md` so Path B/manual generation remains clear.
    """
    if not bool(getattr(brief, "include_images", False)):
        return {"manifest": None, "markdown": None, "status": "disabled"}

    images_dir = project_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = images_dir / "image_prompts.json"

    design_spec = _read(project_dir / "design_spec.md")
    spec_lock = _read(project_dir / "spec_lock.md")
    manifest = _build_manifest(project_dir, brief, design_spec, spec_lock)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _emit(status_callback, "image_manifest", "Created AI image prompt manifest", {"manifest": str(manifest_path)})

    md_path = _run_image_gen_render_md(manifest_path)
    result: dict[str, str | None] = {
        "manifest": str(manifest_path),
        "markdown": str(md_path) if md_path else None,
        "status": "manifest_ready",
    }

    if os.environ.get("CLOUD_GENERATOR_MOCK") == "1":
        _write_mock_images(manifest, images_dir)
        result["status"] = "generated"
        _emit(status_callback, "image_generation", "CLOUD_GENERATOR_MOCK=1 generated placeholder image assets", {"manifest": str(manifest_path)})
        return result

    mode = str(getattr(brief, "image_source_mode", "auto") or "auto").strip().lower()
    if mode == "none":
        _emit(status_callback, "image_generation", "Image source mode is none; prompts saved only", {"manifest": str(manifest_path)})
    elif os.environ.get("IMAGE_BACKEND", "").strip() and mode in {"auto", "generate"}:
        _emit(status_callback, "image_generation", "Running image_gen.py manifest backend", {"manifest": str(manifest_path)})
        try:
            generated, total = _run_image_gen_manifest(manifest_path, images_dir)
            result["status"] = "generated" if generated >= total else "partial"
        except Exception as exc:
            generated, total = _manifest_generation_counts(manifest_path)
            result["status"] = "partial" if generated else "failed"
            result["error"] = str(exc)
            if generated:
                _emit(
                    status_callback,
                    "image_generation",
                    "AI image generation partially completed; manifest remains available",
                    {"generated": generated, "total": total, "error": str(exc)},
                )
            else:
                _emit(status_callback, "image_generation", "AI image generation failed; manifest remains available", {"error": str(exc)})
            if mode == "auto":
                _try_image_search_fallback(manifest_path, images_dir, status_callback)
                result["status"] = "search_attempted"
    elif mode in {"auto", "search"}:
        _emit(status_callback, "image_generation", "IMAGE_BACKEND not configured; trying web image search fallback", {"manifest": str(manifest_path)})
        search_result = _try_image_search_fallback(manifest_path, images_dir, status_callback)
        result["status"] = "searched" if search_result else "manifest_ready"
    else:
        _emit(status_callback, "image_generation", "IMAGE_BACKEND not configured; image prompts saved for manual/native generation", {"manifest": str(manifest_path)})

    return result


def _write_mock_images(manifest: dict[str, Any], images_dir: Path) -> None:
    mock_png = _mock_png_bytes(1280, 720, (91, 141, 239))
    sources: dict[str, Any] = {}
    for item in manifest.get("items") or []:
        filename = str(item.get("filename") or "").strip() or "mock_image.png"
        if not filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            filename = f"{Path(filename).stem or 'mock_image'}.png"
        (images_dir / filename).write_bytes(mock_png)
        sources[filename] = {
            "slide": item.get("slide_index") or "",
            "source": "mock",
            "query": item.get("prompt") or "",
            "attribution": "CLOUD_GENERATOR_MOCK placeholder",
        }
    if sources:
        (images_dir / "image_sources.json").write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")


def _mock_png_bytes(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def _build_manifest(project_dir: Path, brief: Any, design_spec: str, spec_lock: str) -> dict[str, Any]:
    pages = _extract_pages(design_spec)
    if not pages:
        pages = [{"index": 1, "title": "Hero visual", "content": getattr(brief, "user_notes", "") or "PPT supporting visual"}]

    style_anchor = _first_match(design_spec, [r"Style[:：]\s*(.+)", r"Visual Theme[^\n]*\n(.{0,180})"]) or str(getattr(brief, "style", "general"))
    colors = _extract_colors(spec_lock + "\n" + design_spec)
    items: list[dict[str, str]] = []
    selected_pages = pages[: min(4, len(pages))]
    total_pages = max(len(pages), len(selected_pages), 1)
    for page in selected_pages:
        filename = f"{page['index']:02d}_image.png"
        placement = _image_placement_for_page(page, total_pages)
        prompt = (
            f"Create a presentation-ready supporting image for slide {page['index']}: {page['title']}. "
            f"Slide message/content: {page['content']}. "
            f"Deck style: {style_anchor}. Use a coherent palette based on {colors or 'the deck colors'}. "
            f"Composition should suit a {placement['slot']} slide placement: {placement['description']}. "
            "No visible text, no logos, no watermarks. Leave safe margins for slide layout. "
            "Professional, high-quality, suitable for an editable PPT deck."
        )
        items.append({
            "filename": filename,
            "slide_index": str(page["index"]),
            "purpose": f"Supporting visual for slide {page['index']}",
            "type": "supporting_illustration",
            "placement_slot": placement["slot"],
            "layout_pattern": placement["layout_pattern"],
            "svg_box": placement["svg_box"],
            "preserve_aspect_ratio": placement["preserve_aspect_ratio"],
            "composition_guidance": placement["description"],
            "prompt": prompt,
            "aspect_ratio": "16:9",
            "image_size": "1K",
            "status": "Pending",
            "alt_text": f"Supporting visual for {page['title']}",
        })

    return {
        "project": project_dir.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "deck_style_anchor": style_anchor,
        "color_scheme": {"deck": colors} if colors else {},
        "items": items,
    }


def _image_placement_for_page(page: dict[str, Any], total_pages: int) -> dict[str, str]:
    """Choose a PPT Master-style image slot for a page.

    This mirrors the original project's layout library: image position follows
    information weight and page rhythm instead of using one fixed right rail.
    """
    index = int(page.get("index") or 1)
    title = str(page.get("title") or "")
    content = str(page.get("content") or "")
    text = f"{title} {content}".lower()

    if index == 1:
        return {
            "slot": "full_bleed_hero",
            "layout_pattern": "Full-bleed + floating text",
            "svg_box": "0,0,1280,720",
            "preserve_aspect_ratio": "xMidYMid slice",
            "description": "image fills the canvas; reserve a quiet focal area for floating title text with an overlay.",
        }
    if index == total_pages or any(key in text for key in ["summary", "conclusion", "takeaway", "总结", "结论", "收束"]):
        return {
            "slot": "center_hero",
            "layout_pattern": "Negative-space-driven",
            "svg_box": "330,130,620,350",
            "preserve_aspect_ratio": "xMidYMid meet",
            "description": "single central visual with generous whitespace; text sits above or below, not crowded.",
        }
    if any(key in text for key in ["timeline", "process", "roadmap", "流程", "路径", "时间线", "进度"]):
        return {
            "slot": "top_wide",
            "layout_pattern": "Top-bottom split",
            "svg_box": "80,92,1120,290",
            "preserve_aspect_ratio": "xMidYMid slice",
            "description": "wide horizontal visual band on top; detailed explanation or steps live below.",
        }
    if any(key in text for key in ["case", "story", "customer", "案例", "故事", "客户"]):
        return {
            "slot": "overlap_figure",
            "layout_pattern": "Figure-text overlap",
            "svg_box": "560,112,600,430",
            "preserve_aspect_ratio": "xMidYMid slice",
            "description": "dominant figure block with headline or number overlapping one edge.",
        }
    if index % 2 == 0:
        return {
            "slot": "right_dominant",
            "layout_pattern": "Asymmetric split 3:7",
            "svg_box": "700,118,500,460",
            "preserve_aspect_ratio": "xMidYMid slice",
            "description": "image carries more visual weight on the right; text occupies a narrower left column.",
        }
    return {
        "slot": "left_supporting",
        "layout_pattern": "Asymmetric split 7:3",
        "svg_box": "80,138,460,420",
        "preserve_aspect_ratio": "xMidYMid slice",
        "description": "supporting visual anchors the left; key message and structured text sit on the right.",
    }


def _extract_pages(design_spec: str) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    in_outline = False
    for line in design_spec.splitlines():
        stripped = line.strip()
        heading = re.match(r"^#{1,4}\s+(.+)$", stripped)
        if heading:
            title = heading.group(1).strip().lower()
            in_outline = "page outline" in title or "slide outline" in title or "页面大纲" in title or "页大纲" in title
            continue
        if not in_outline:
            continue
        row = re.match(r"^\|\s*(?:P)?(\d{1,2}|第\s*\d+\s*页|Slide\s*\d+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", stripped, re.IGNORECASE)
        if row:
            if row.group(2).strip().lower() in {"title", "page", "页面", "页码"}:
                continue
            index = _page_number(row.group(1), len(pages) + 1)
            pages.append({"index": index, "title": row.group(2).strip(), "content": row.group(3).strip()})
            continue
        bullet = re.match(r"^(?:[-*]\s*)?(?:P|Slide\s*)?(\d{1,2})[.)：:\-\s]+(.+)", stripped, re.IGNORECASE)
        if bullet and not stripped.startswith("#"):
            index = int(bullet.group(1))
            title = bullet.group(2).strip()
            if title:
                pages.append({"index": index, "title": title[:80], "content": title})
    return pages


def _page_number(value: str, fallback: int) -> int:
    match = re.search(r"\d+", value)
    return int(match.group(0)) if match else fallback


def _extract_colors(text: str) -> str:
    colors = []
    for color in re.findall(r"#[0-9A-Fa-f]{6}", text):
        if color not in colors:
            colors.append(color)
        if len(colors) >= 5:
            break
    return ", ".join(colors)


def _first_match(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            return " ".join(match.group(1).split())[:220]
    return ""


def _run_image_gen_render_md(manifest_path: Path) -> Path | None:
    script = SCRIPTS_DIR / "image_gen.py"
    result = subprocess.run(
        [sys.executable, str(script), "--render-md", str(manifest_path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    (manifest_path.parent / "image_gen_render_md.log").write_text(redact_secrets(result.stdout + "\n" + result.stderr), encoding="utf-8")
    if result.returncode != 0:
        return None
    md_path = manifest_path.with_suffix(".md")
    return md_path if md_path.exists() else None


def _manifest_generation_counts(manifest_path: Path) -> tuple[int, int]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return 0, 0
    items = manifest.get("items") if isinstance(manifest, dict) else []
    if not isinstance(items, list):
        return 0, 0
    generated = sum(1 for item in items if str(item.get("status") or "").lower() in {"generated", "websourced"})
    return generated, len(items)


def _run_image_gen_manifest(manifest_path: Path, output_dir: Path) -> tuple[int, int]:
    script = SCRIPTS_DIR / "image_gen.py"
    timeout = int(os.environ.get("IMAGE_MANIFEST_TIMEOUT_SECONDS", "1200") or "1200")
    concurrency = os.environ.get("IMAGE_MANIFEST_CONCURRENCY", "").strip()
    cmd = [sys.executable, str(script), "--manifest", str(manifest_path), "-o", str(output_dir)]
    if concurrency:
        cmd.extend(["--concurrency", concurrency])
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        partial = (exc.stdout or "") + "\n" + (exc.stderr or "")
        (output_dir / "image_gen_manifest.log").write_text(redact_secrets(partial), encoding="utf-8")
        generated, total = _manifest_generation_counts(manifest_path)
        raise RuntimeError(f"image_gen.py timed out after {timeout} seconds ({generated}/{total} images generated)") from exc
    (output_dir / "image_gen_manifest.log").write_text(redact_secrets(result.stdout + "\n" + result.stderr), encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(redact_secrets(result.stderr or result.stdout or "image_gen.py failed"))
    return _manifest_generation_counts(manifest_path)


def _try_image_search_fallback(manifest_path: Path, output_dir: Path, status_callback=None) -> bool:
    if os.environ.get("PPT_MASTER_IMAGE_SEARCH_FALLBACK", "1").strip().lower() in {"0", "false", "no"}:
        _emit(status_callback, "image_generation", "Web image search fallback disabled", {"manifest": str(manifest_path)})
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        _emit(status_callback, "image_generation", "Image search fallback skipped; manifest unreadable", {"error": str(exc)})
        return False

    script = SCRIPTS_DIR / "image_search.py"
    any_success = False
    log_lines: list[str] = []
    for item in manifest.get("items", [])[:4]:
        filename = str(item.get("filename") or "").strip()
        if not filename:
            continue
        if str(item.get("status") or "").lower() == "generated" or (output_dir / filename).exists():
            continue
        query = _search_query_for_item(item)
        if not query:
            continue
        slide = str(item.get("slide_index") or "")
        cmd = [
            sys.executable,
            str(script),
            query,
            "--filename",
            filename,
            "-o",
            str(output_dir),
            "--orientation",
            "landscape",
            "--purpose",
            str(item.get("purpose") or "supporting visual"),
            "--slide",
            slide,
            "--no-candidates",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
        log_lines.extend([f"$ {' '.join(cmd)}", redact_secrets(result.stdout), redact_secrets(result.stderr), ""])
        if result.returncode == 0 and (output_dir / filename).exists():
            item["status"] = "WebSourced"
            item["source"] = "image_search.py"
            any_success = True
            _emit(status_callback, "image_generation", "Web image selected for slide", {"slide": slide, "filename": filename, "query": query})
        else:
            item["status"] = item.get("status") or "Pending"

    (output_dir / "image_search_fallback.log").write_text(redact_secrets("\n".join(log_lines)), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _run_image_gen_render_md(manifest_path)
    return any_success


def _search_query_for_item(item: dict[str, Any]) -> str:
    prompt = str(item.get("prompt") or "")
    alt = str(item.get("alt_text") or item.get("purpose") or "")
    text = f"{alt} {prompt}"
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff\s-]+", " ", text)
    words = [word for word in text.split() if len(word) > 1]
    return " ".join(words[:8])


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _emit(callback, status: str, message: str, metadata: dict[str, Any]) -> None:
    if callback:
        callback(status, message, metadata)
