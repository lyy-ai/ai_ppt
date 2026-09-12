#!/usr/bin/env python3
"""Cloud Executor — generates svg_output/*.svg and notes/*.md from spec_lock.md.

Replaces the IDE-agent Executor role with server-side LLM calls.
Pages are generated sequentially; each page gets the full spec_lock context.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .llm_client import chat_completion, resolve_model_name

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
CHARTS_INDEX = SKILL_DIR / "templates" / "charts" / "charts_index.json"
DISALLOWED_SVG_PATTERNS = [
    (re.compile(r"<\s*style\b", re.IGNORECASE), "<style> is not allowed"),
    (re.compile(r"<\s*foreignObject\b", re.IGNORECASE), "<foreignObject> is not allowed"),
    (re.compile(r"<\s*animate\w*\b", re.IGNORECASE), "<animate*> is not allowed"),
    (re.compile(r"<\s*script\b", re.IGNORECASE), "<script> is not allowed"),
    (re.compile(r"<\s*iframe\b", re.IGNORECASE), "<iframe> is not allowed"),
    (re.compile(r"<\s*use\b", re.IGNORECASE), "<use> is not allowed"),
    (re.compile(r"rgba\s*\(", re.IGNORECASE), "rgba() is not allowed"),
]


def _parse_spec_lock(text: str) -> dict[str, Any]:
    """Parse spec_lock.md into a dict of sections."""
    sections: dict[str, Any] = {}
    current_section: str | None = None
    current_lines: list[str] = []

    for line in text.splitlines():
        if line.startswith("## "):
            if current_section:
                sections[current_section] = "\n".join(current_lines)
            current_section = line[3:].strip()
            current_lines = []
        elif current_section:
            current_lines.append(line)

    if current_section:
        sections[current_section] = "\n".join(current_lines)

    return sections


def _parse_page_outline(design_spec: str) -> list[dict[str, str]]:
    """Extract page outline from design_spec.md."""
    pages: list[dict[str, str]] = []
    in_outline = False
    current_page: dict[str, str] | None = None
    current_content: list[str] = []

    def flush_current_page() -> None:
        nonlocal current_page, current_content
        if current_page:
            content = "\n".join(current_content).strip()
            if content:
                current_page["content"] = content
            pages.append(current_page)
        current_page = None
        current_content = []

    for line in design_spec.splitlines():
        stripped = line.strip()
        if re.match(
            r"^#{1,3}\s.*(?:outline|page.*roster|content.*outline|页面大纲|页.*大纲|页面.*(?:对照|规划|列表|清单)|页.*(?:对照|规划|列表|清单))",
            stripped,
            re.IGNORECASE,
        ):
            in_outline = True
            continue

        if in_outline and stripped.startswith("#") and not re.match(r"^#{2,4}\s*P\d+", stripped, re.IGNORECASE):
            flush_current_page()
            break

        if in_outline:
            page_bullet = re.match(
                r"^(?:[-*]\s*)?(P\d{1,2}|第\s*\d+\s*页|Slide\s*\d+)\s*(?:[\(（]([^\)）]+)[\)）])?\s*(?:[-:：]\s*)?(.+)$",
                stripped,
                re.IGNORECASE,
            )
            if page_bullet:
                title = (page_bullet.group(2) or page_bullet.group(1)).strip()
                content = (page_bullet.group(3) or title).strip()
                pages.append({"title": title, "content": content})
                continue

            plain_page = re.match(
                r"^(P\d{1,2}|第\s*\d+\s*页|Slide\s*\d+)\s+(.+?)(?:[:：]\s*(.+))?$",
                stripped,
                re.IGNORECASE,
            )
            if plain_page:
                title = plain_page.group(2).strip()
                content = (plain_page.group(3) or title).strip()
                pages.append({"title": title, "content": content})
                continue

            table_row = re.match(r"^\|\s*(P\d+|第\s*\d+\s*页|Slide\s*\d+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", stripped, re.IGNORECASE)
            if table_row:
                title = table_row.group(2).strip()
                content = table_row.group(3).strip()
                pages.append({"title": title, "content": content})
                continue

            heading = re.match(
                r"^#{2,4}\s*(P\d+|第\s*\d+\s*页|Slide\s*\d+)\s*(?:[-:：]\s*)?(.+)?$",
                stripped,
                re.IGNORECASE,
            )
            if heading:
                flush_current_page()
                label = heading.group(1).strip()
                title = (heading.group(2) or label).strip()
                current_page = {"title": title, "content": title}
                continue

            bullet = re.match(r"^(?:\d+[\.\)]\s*|\*\s*|\-\s*|P\d+[:：]\s*)(.+)", stripped)
            if bullet:
                text = bullet.group(1).strip()
                if current_page:
                    current_content.append(text)
                else:
                    pages.append({"title": text, "content": text})
                continue

            if current_page and stripped:
                current_content.append(stripped)

    flush_current_page()

    return pages


def _load_image_resource(project_dir: Path, page_index: int) -> str:
    manifest_path = project_dir / "images" / "image_prompts.json"
    if not manifest_path.exists():
        return ""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return ""

    for item in manifest.get("items", []):
        try:
            slide_index = int(item.get("slide_index") or 0)
        except (TypeError, ValueError):
            slide_index = 0
        if slide_index != page_index + 1:
            continue

        filename = str(item.get("filename") or "").strip()
        if not filename:
            continue
        image_path = project_dir / "images" / filename
        attribution = _image_attribution(project_dir, filename)
        prompt = str(item.get("prompt") or "").strip()
        alt_text = str(item.get("alt_text") or item.get("purpose") or "").strip()
        placement_slot = str(item.get("placement_slot") or "supporting_visual").strip()
        layout_pattern = str(item.get("layout_pattern") or "Asymmetric split").strip()
        svg_box = str(item.get("svg_box") or "").strip()
        preserve_aspect_ratio = str(item.get("preserve_aspect_ratio") or "xMidYMid slice").strip()
        composition_guidance = str(item.get("composition_guidance") or "").strip()
        if image_path.exists():
            return (
                f"Available raster image for this slide: ../images/{filename}\n"
                f"- Use this image exactly once with an SVG <image href=\"../images/{filename}\" ...> element.\n"
                f"- Placement slot: {placement_slot}\n"
                f"- Layout pattern: {layout_pattern}\n"
                f"- Preferred SVG box: {svg_box or 'choose a box from the page layout, never a default thumbnail'}\n"
                f"- preserveAspectRatio: {preserve_aspect_ratio}\n"
                f"- Composition guidance: {composition_guidance}\n"
                f"- Attribution: {attribution or 'none required or unavailable'}\n"
                "- Follow the slot unless it clearly conflicts with the page content. Do not place every image in the same right-side card.\n"
                "- If attribution is present, add a tiny readable credit near the image or slide footer.\n"
                f"- Alt/purpose: {alt_text}\n"
            )
        return (
            "AI image prompt exists for this slide, but the raster file has not been generated yet.\n"
            f"- Expected file: images/{filename}\n"
            f"- Placement slot: {placement_slot}\n"
            f"- Layout pattern: {layout_pattern}\n"
            f"- Preferred SVG box: {svg_box or 'choose from the page structure'}\n"
            f"- preserveAspectRatio: {preserve_aspect_ratio}\n"
            f"- Composition guidance: {composition_guidance}\n"
            f"- Prompt intent: {prompt[:900]}\n"
            "- Create a clean SVG-native placeholder illustration block in the same location and style.\n"
            "- Do not use an <image> element unless the raster file exists.\n"
        )
    return ""


def _image_attribution(project_dir: Path, filename: str) -> str:
    manifest_path = project_dir / "images" / "image_sources.json"
    if not manifest_path.exists():
        return ""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    for item in manifest.get("items", []):
        if str(item.get("filename") or "") == filename:
            return str(item.get("attribution_text") or item.get("credit") or "").strip()
    return ""


def _chart_template_guidance(design_spec: str, spec_lock: str, page_index: int) -> str:
    page_label = f"P{page_index + 1:02d}"
    patterns = [
        rf"{page_label}[^\n]*(?:chart_template|chart template|图表模板)\s*[=:：]\s*([A-Za-z0-9_\-]+)",
        rf"(?:chart_template|chart template|图表模板)\s*[=:：]\s*([A-Za-z0-9_\-]+)[^\n]*{page_label}",
    ]
    key = ""
    combined = design_spec + "\n" + spec_lock
    for pattern in patterns:
        match = re.search(pattern, combined, re.IGNORECASE)
        if match:
            key = match.group(1).strip()
            break
    if not key:
        return "No chart_template is specified for this page. Use freeform PPT Master layout principles."

    summary = ""
    if CHARTS_INDEX.exists():
        try:
            data = json.loads(CHARTS_INDEX.read_text(encoding="utf-8"))
            chart = (data.get("charts") or {}).get(key) if isinstance(data, dict) else None
            if isinstance(chart, dict):
                summary = str(chart.get("summary") or "")
        except Exception:
            summary = ""
    return (
        f"Use PPT Master chart_template `{key}` as the structural reference for this slide.\n"
        f"Selection rule: {summary or 'Follow the template name and page content shape.'}\n"
        "- Recreate the structure as clean SVG primitives; do not reference the template SVG file by path.\n"
        "- Keep all text editable as <text> elements and all major chart parts grouped with semantic ids.\n"
    )


def _build_svg_prompt(
    spec_lock: str,
    design_spec: str,
    page_index: int,
    total_pages: int,
    page_info: dict[str, str],
    source_content: str,
    image_resource: str = "",
    previous_error: str = "",
) -> str:
    """Build the LLM prompt for a single SVG page."""
    repair_instruction = ""
    if previous_error:
        repair_instruction = f"""
## Previous Attempt Failed
Error:
{previous_error}

Repair the slide by returning a fresh valid JSON object. Do not explain the error.
"""
    image_generation_section = _parse_spec_lock(spec_lock).get("image_generation", "")
    images_enabled = bool(
        re.search(r"\benabled\b", image_generation_section, re.IGNORECASE)
        or re.search(r"image_generation\s*[:=-]\s*enabled", spec_lock, re.IGNORECASE)
        or re.search(r"Images:\s*yes|Image Plan|generated supporting illustrations", design_spec, re.IGNORECASE)
    )
    image_instruction = (
        "AI image generation is ENABLED for this deck. Use the page-specific image resource below when a generated raster file is available. If no generated file exists yet, create an SVG-native placeholder illustration or pictorial block that supports the slide message. Do not reference remote image URLs or copyrighted assets."
        if images_enabled
        else "AI image generation is disabled. Do not create large illustrative image scenes; use restrained shapes, icons, and layout only."
    )
    chart_guidance = _chart_template_guidance(design_spec, spec_lock, page_index)

    return f"""You are a presentation slide designer. Generate ONE SVG slide.

## Design Contract (spec_lock.md)
{spec_lock[:3000]}

## Page Info
- Page {page_index + 1} of {total_pages}
- Title: {page_info.get("title", f"Slide {page_index + 1}")}

## Current Page Outline
{page_info.get("content", page_info.get("title", f"Slide {page_index + 1}"))}

## Source Content
{source_content[:4000]}
{repair_instruction}

## Image Generation Mode
{image_instruction}

## Page Image Resource
{image_resource or "No page-specific raster image resource is available."}

## PPT Master Chart Template Guidance
{chart_guidance}

## Speaker Notes Requirements (PPT Master style)
- Write notes as pure spoken narration from the presenter's first-person perspective, as if I am explaining this page to the audience live.
- Do NOT describe the slide design or say "this slide/page shows", "本页展示", "这是一页", "整体采用", or similar page-description language.
- The body should be 2-5 natural sentences in the deck language. It should sound like a presenter speaking, not an annotation.
- Start with the page's core takeaway or a natural transition from the previous page. For example: "接下来，我们看..." / "在明确了...之后..." / "Having framed X, let's turn to Y."
- Include the key evidence, explanation, or implication from the current page in flowing prose.
- No bracketed stage markers, no labels like "Key points:", "Duration:", "要点：", "时长：", and no markdown bullet lists.
- Make numbers TTS-friendly when useful, especially in Chinese.

## SVG Requirements
- viewBox: 0 0 1280 720
- Use ONLY colors from spec_lock
- Use ONLY fonts from spec_lock
- Wrap meaningful visual objects in <g id="..."> groups so PPT native animations can target them.
- Use semantic group ids such as cover-title, title-main, subtitle, card-1, step-1, chart-main, figure-hero, takeaway.
- Do not leave important objects anonymous unless they are purely decorative chrome.
- Image placement must follow PPT Master layout principles: information weight first, not a fixed template.
- For page-specific raster images, use the Preferred SVG box when provided. Interpret it as x,y,width,height.
- Use full_bleed_hero only for cover/feature pages, with overlay or gradient shapes to protect text contrast.
- Use right_dominant / left_supporting for asymmetric split pages; keep text in the opposite column.
- Use top_wide for process/timeline pages; place explanation below the image band.
- Use center_hero for summary pages with whitespace around the image.
- Use preserveAspectRatio exactly as specified by the image resource unless you intentionally choose meet for an uncropped figure.
- NO <style>, <foreignObject>, <animate>, <script>, <iframe>, <symbol>+<use>
- NO rgba(), NO HTML named entities (use raw Unicode)
- Write text as raw Unicode, XML-escape only & < > " '
- All text in <text> elements with font-family from spec_lock
- Clean, professional layout appropriate for the page's role

## Output Format
Respond with a JSON object:

```json
{{
  "svg": "<svg ...>...</svg>",
  "notes": "Pure spoken speaker notes only, first-person presenter narration, no heading"
}}
```

Return ONLY the JSON object, no other text."""


def _call_llm(prompt: str, *, profile: str = "slide") -> str:
    return chat_completion(
        system=(
            "You are an SVG slide designer. Always respond with valid JSON only. "
            "Generate clean, professional presentation slides. "
            "Never output raw <, >, or & inside text content; XML-escape them."
        ),
        user=prompt,
        temperature=0.35,
        max_tokens=8192,
        profile=profile,
    )


def _parse_svg_response(raw: str) -> dict[str, str]:
    """Extract SVG and notes from LLM JSON response."""
    raw = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    json_text = fenced.group(1).strip() if fenced else raw
    if not fenced:
        start = json_text.find("{")
        end = json_text.rfind("}")
        if start >= 0 and end > start:
            json_text = json_text[start:end + 1]
    try:
        data = json.loads(json_text)
        return {"svg": data.get("svg", ""), "notes": data.get("notes", "")}
    except json.JSONDecodeError:
        svg = _extract_svg_fragment(raw)
        if svg:
            return {"svg": svg, "notes": ""}
        raise


def _extract_svg_fragment(raw: str) -> str:
    fenced_svg = re.search(r"```(?:svg|xml)?\s*(<svg\b.*?</svg>)\s*```", raw, re.DOTALL | re.IGNORECASE)
    if fenced_svg:
        return fenced_svg.group(1).strip()
    match = re.search(r"<svg\b.*?</svg>", raw, re.DOTALL | re.IGNORECASE)
    return match.group(0).strip() if match else ""


def _strip_invalid_xml_chars(value: str) -> str:
    return "".join(
        ch for ch in value
        if ch in "\t\n\r" or ord(ch) >= 0x20
    )


def _repair_text_angle_brackets(svg: str) -> str:
    """Escape common raw comparison brackets inside leaf text/tspan nodes."""
    def repair(match: re.Match[str]) -> str:
        open_tag, text, close_tag = match.groups()
        if "<" in text or ">" in text:
            return match.group(0)
        text = re.sub(r"<(?!/?tspan\b)", "&lt;", text)
        text = re.sub(r"(?<!])>", "&gt;", text)
        return f"{open_tag}{text}{close_tag}"

    svg = re.sub(r"(<text\b[^>]*>)([^<>]*)(</text>)", repair, svg, flags=re.DOTALL | re.IGNORECASE)
    svg = re.sub(r"(<tspan\b[^>]*>)([^<>]*)(</tspan>)", repair, svg, flags=re.DOTALL | re.IGNORECASE)
    return svg


def _repair_common_unclosed_text(svg: str) -> str:
    svg = re.sub(r"(<text\b[^>]*>[^<]*?)(?=\s*<(?:rect|circle|path|line|g|image|text)\b)", r"\1</text>", svg, flags=re.IGNORECASE)
    return svg


def _remove_group_opacity(svg: str) -> str:
    """PPT Master quality gate forbids <g opacity>; child opacity is safer."""
    return re.sub(r"(<g\b[^>]*)\s+opacity=(['\"])[^'\"]+\2", r"\1", svg, flags=re.IGNORECASE)


def _sanitize_svg(svg: str) -> str:
    """Normalize common LLM SVG mistakes before writing to disk."""
    start = svg.find("<svg")
    end = svg.rfind("</svg>")
    if start >= 0 and end >= 0:
        svg = svg[start:end + len("</svg>")]

    svg = _strip_invalid_xml_chars(svg)
    svg = _repair_common_unclosed_text(svg)
    svg = _repair_text_angle_brackets(svg)
    svg = _remove_group_opacity(svg)
    svg = re.sub(
        r'font-family="[^"<>\n]*"\s+(?=(?:font-size|font-weight|fill|stroke|text-anchor|dominant-baseline)=)',
        'font-family="Arial, Microsoft YaHei, sans-serif" ',
        svg,
    )
    svg = re.sub(
        r"font-family='[^'<>\n]*'\s+(?=(?:font-size|font-weight|fill|stroke|text-anchor|dominant-baseline)=)",
        'font-family="Arial, Microsoft YaHei, sans-serif" ',
        svg,
    )
    svg = re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#[0-9]+;|#x[0-9A-Fa-f]+;)", "&amp;", svg)

    try:
        ET.fromstring(svg)
    except ET.ParseError as exc:
        raise ValueError(f"Generated SVG is not valid XML: {exc}") from exc

    return svg


def _validate_svg(svg: str) -> list[str]:
    """Return validation errors for generated SVG."""
    errors: list[str] = []
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as exc:
        return [f"invalid XML: {exc}"]

    tag_name = root.tag.split("}")[-1]
    if tag_name != "svg":
        errors.append("root element must be <svg>")

    view_box = root.attrib.get("viewBox", "")
    if view_box.strip() != "0 0 1280 720":
        errors.append(f"viewBox must be '0 0 1280 720', got '{view_box}'")

    for pattern, message in DISALLOWED_SVG_PATTERNS:
        if pattern.search(svg):
            errors.append(message)

    text_count = sum(1 for element in root.iter() if element.tag.split("}")[-1] == "text")
    if text_count == 0:
        errors.append("slide must contain at least one <text> element")

    return errors


def _normalize_speaker_notes(notes: str, page_info: dict[str, str], page_index: int, total_pages: int) -> str:
    """Keep notes as PPT Master-style spoken narration instead of slide commentary."""
    text = (notes or "").strip()
    text = re.sub(r"^#+\s*Speaker Notes\s*", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"^#+\s*.*\n+", "", text).strip()
    text = re.sub(r"(?m)^\s*(?:[-*]|\d+[\.)])\s*", "", text).strip()
    text = re.sub(r"(?m)^\s*\*\*[^*\n]{1,24}\*\*[：:]\s*", "", text).strip()
    text = re.sub(r"(?m)^\s*(?:开场引导|时间轴讲解|讲解重点|要点|Key points?|Duration|时长)[：:].*$", "", text, flags=re.IGNORECASE).strip()
    if not text or text.lower() == "no speaker notes.":
        title = str(page_info.get("title") or f"Slide {page_index + 1}").strip()
        content = str(page_info.get("content") or title).strip()
        if page_index == 0:
            return f"大家好，今天我想先用这一页建立我们要讨论的主题：{title}。围绕这个主题，我会结合{content}，帮助大家快速抓住这份内容的主线。"
        return f"接下来，我们把视角切到{title}。这一部分的重点是{content}，我会用它来说明前面结论背后的关键原因和实际含义。"

    slide_description_patterns = [
        r"^这是一(?:张|页|套).*?(?:PPT|幻灯片|页面|slide).*?[，,。]\s*",
        r"^本页(?:主要)?(?:展示|介绍|呈现|说明).*?[，,。]\s*",
        r"^这一页(?:主要)?(?:展示|介绍|呈现|说明).*?[，,。]\s*",
        r"^整体(?:采用|使用).*?[，,。]\s*",
        r"^This slide (?:shows|presents|introduces|explains).*?[,.]\s*",
        r"^This page (?:shows|presents|introduces|explains).*?[,.]\s*",
    ]
    original = text
    for pattern in slide_description_patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()
    visual_sentence_pattern = re.compile(
        r"[^。.!?！？]*(?:整体采用|布局|配色|主标题|副标题|留白|点缀|图形|造型|视觉|画面|页面|PPT|幻灯片|slide design|layout|visual)[^。.!?！？]*[。.!?！？]?",
        re.IGNORECASE,
    )
    text = visual_sentence_pattern.sub("", text).strip()
    if text != original:
        transition = "首先，" if page_index == 0 else "接下来，"
        if re.search(r"[\u4e00-\u9fff]", text):
            text = transition + text
        else:
            text = ("To start, " if page_index == 0 else "Next, ") + text[:1].lower() + text[1:]
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        title = str(page_info.get("title") or f"Slide {page_index + 1}").strip()
        content = str(page_info.get("content") or title).strip()
        if page_index == 0:
            return f"大家好，今天我想先从{title}讲起。我们会围绕{content}建立一个共同理解，方便后面继续展开。"
        return f"接下来，我们看{title}。这一页的重点是{content}，请大家先抓住其中最关键的结论和它对实际工作的启发。"
    return text


def run_executor(
    project_dir: str | Path,
    source_md_path: str | Path,
    include_notes: bool = True,
    max_attempts: int = 2,
    status_callback=None,
) -> dict[str, Any]:
    """Run the Executor phase: spec_lock -> svg_output/*.svg + notes/*.md.

    Returns dict with paths and page count.
    """
    project_dir = Path(project_dir)
    spec_lock_text = (project_dir / "spec_lock.md").read_text(encoding="utf-8")
    design_spec_text = ""
    design_spec_path = project_dir / "design_spec.md"
    if design_spec_path.exists():
        design_spec_text = design_spec_path.read_text(encoding="utf-8")

    source_content = Path(source_md_path).read_text(encoding="utf-8")
    pages = _parse_page_outline(design_spec_text)
    if not pages:
        raise RuntimeError("No pages found in design_spec.md Page Outline")

    svg_dir = project_dir / "svg_output"
    notes_dir = project_dir / "notes"
    svg_dir.mkdir(parents=True, exist_ok=True)
    notes_dir.mkdir(parents=True, exist_ok=True)

    all_notes: list[str] = []
    generated: list[str] = []
    report: dict[str, Any] = {
        "page_count_expected": len(pages),
        "page_count_generated": 0,
        "max_attempts": max_attempts,
        "pages": [],
    }

    for i, page_info in enumerate(pages):
        if status_callback:
            status_callback("page_generating", f"Generating slide {i + 1} / {len(pages)}", {
                "page": i + 1,
                "total": len(pages),
                "title": page_info.get("title", f"Slide {i + 1}"),
            })
        previous_error = ""
        result: dict[str, str] = {"svg": "", "notes": ""}
        svg = ""
        page_report: dict[str, Any] = {
            "page": i + 1,
            "title": page_info.get("title", f"Slide {i + 1}"),
            "attempts": [],
            "status": "failed",
        }

        for attempt in range(1, max_attempts + 1):
            prompt = _build_svg_prompt(
                spec_lock_text, design_spec_text,
                i, len(pages), page_info, source_content,
                image_resource=_load_image_resource(project_dir, i),
                previous_error=previous_error,
            )
            profile = "slide" if attempt == 1 else "slide_fallback"
            model_name = resolve_model_name(profile=profile)
            if status_callback:
                status_callback("page_attempting", f"Generating slide {i + 1} / {len(pages)}, attempt {attempt}", {
                    "page": i + 1,
                    "total": len(pages),
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "model_profile": profile,
                    "model_name": model_name,
                })
            try:
                raw = _call_llm(prompt, profile=profile)
                attempts_dir = project_dir / "llm_attempts"
                attempts_dir.mkdir(parents=True, exist_ok=True)
                (attempts_dir / f"page_{i + 1:02d}_attempt_{attempt:02d}.txt").write_text(raw, encoding="utf-8")
                result = _parse_svg_response(raw)
                svg = _sanitize_svg(result["svg"])
                validation_errors = _validate_svg(svg)
                if validation_errors:
                    raise ValueError("; ".join(validation_errors))
            except Exception as exc:
                previous_error = str(exc)
                page_report["attempts"].append({
                    "attempt": attempt,
                    "status": "failed",
                    "error": previous_error,
                    "model_profile": profile,
                    "model_name": model_name,
                })
                if status_callback:
                    status_callback("page_attempt_failed", f"Slide {i + 1} attempt {attempt} failed", {
                        "page": i + 1,
                        "total": len(pages),
                        "attempt": attempt,
                        "max_attempts": max_attempts,
                        "model_profile": profile,
                        "model_name": model_name,
                        "error": previous_error[:500],
                    })
                continue

            page_report["attempts"].append({
                "attempt": attempt,
                "status": "succeeded",
                "error": "",
                "model_profile": profile,
                "model_name": model_name,
            })
            page_report["status"] = "succeeded"
            break

        if page_report["status"] != "succeeded":
            report["pages"].append(page_report)
            report_path = project_dir / "generation_report.json"
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            raise RuntimeError(
                f"Failed to generate valid SVG for page {i + 1}: {previous_error}"
            )

        page_num = str(i + 1).zfill(2)
        svg_path = svg_dir / f"{page_num}_{page_info.get('title', f'slide_{page_num}')[:30]}.svg"
        svg_path = svg_dir / f"{page_num}_slide.svg"
        svg_path.write_text(svg, encoding="utf-8")
        generated.append(str(svg_path))
        page_report["svg_path"] = str(svg_path)

        if include_notes:
            notes_text = _normalize_speaker_notes(result["notes"], page_info, i, len(pages))
            notes_path = notes_dir / f"{page_num}_slide.md"
            notes_path.write_text(notes_text, encoding="utf-8")
            all_notes.append(f"# {page_num}_slide\n\n{notes_text}")
            page_report["notes_path"] = str(notes_path)

        if status_callback:
            status_callback("page_completed", f"Generated slide {i + 1} / {len(pages)}", {
                "page": i + 1,
                "total": len(pages),
                "title": page_info.get("title", f"Slide {i + 1}"),
            })
        report["pages"].append(page_report)

    if include_notes and all_notes:
        total_notes_path = notes_dir / "total.md"
        total_notes_path.write_text("\n\n---\n\n".join(all_notes), encoding="utf-8")

    report["page_count_generated"] = len(generated)
    report_path = project_dir / "generation_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "svg_dir": str(svg_dir),
        "notes_dir": str(notes_dir),
        "page_count": len(generated),
        "svg_files": generated,
        "report": str(report_path),
    }
