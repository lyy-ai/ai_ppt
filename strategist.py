#!/usr/bin/env python3
"""Cloud Strategist — generates design_spec.md and spec_lock.md from a Brief + source Markdown.

Replaces the IDE-agent Strategist role with a server-side LLM call.
No interactive confirmations — the Brief already encodes user choices.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .brief_schema import Brief
from .llm_client import chat_completion

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
SPEC_LOCK_TEMPLATE = SKILL_DIR / "templates" / "spec_lock_reference.md"
DESIGN_SPEC_TEMPLATE = SKILL_DIR / "templates" / "design_spec_reference.md"
CHARTS_INDEX = SKILL_DIR / "templates" / "charts" / "charts_index.json"


def _read_template_context(project_dir: Path) -> str:
    template_dir = project_dir / "templates"
    if not template_dir.exists():
        return "No template selected."

    parts: list[str] = []
    design_spec = template_dir / "design_spec.md"
    if design_spec.exists():
        parts.append("## Template design_spec.md\n" + design_spec.read_text(encoding="utf-8", errors="replace")[:5000])

    svg_parts = []
    for svg_path in sorted(template_dir.glob("*.svg"))[:6]:
        svg_parts.append(f"### {svg_path.name}\n{svg_path.read_text(encoding='utf-8', errors='replace')[:2200]}")
    if svg_parts:
        parts.append("## Template SVG roster\n" + "\n\n".join(svg_parts))

    asset_names = [
        str(path.relative_to(template_dir))
        for path in sorted(template_dir.rglob("*"))
        if path.is_file() and path.name != "design_spec.md"
    ][:80]
    if asset_names:
        parts.append("## Template asset files\n" + "\n".join(asset_names))

    return "\n\n".join(parts) if parts else "Template directory selected, but no readable design_spec/SVG assets were found."


def _read_chart_catalog(limit: int = 71) -> str:
    if not CHARTS_INDEX.exists():
        return "Chart template catalog is unavailable."
    try:
        data = json.loads(CHARTS_INDEX.read_text(encoding="utf-8"))
    except Exception:
        return "Chart template catalog could not be read."
    charts = data.get("charts") if isinstance(data, dict) else {}
    if not isinstance(charts, dict) or not charts:
        return "Chart template catalog is empty."
    lines: list[str] = []
    for key, value in list(charts.items())[:limit]:
        summary = ""
        if isinstance(value, dict):
            summary = str(value.get("summary") or "")
        lines.append(f"- {key}: {summary[:180]}")
    return "\n".join(lines)


def _build_strategist_prompt(brief: Brief, source_content: str) -> str:
    """Build the LLM prompt that replaces the Strategist role."""
    image_direction = (
        "AI-generated supporting illustrations are ENABLED. For pages that benefit from visuals, "
        "define concise image intent: subject, composition, style, and how it supports the slide message. "
        "Keep image direction consistent with the deck palette and style. Use SVG-native illustration plans; "
        "do not require external copyrighted imagery."
        if brief.include_images
        else "AI-generated supporting illustrations are disabled. Prefer text, shapes, icons, and charts only."
    )
    template_context = _read_template_context(Path(getattr(brief, "_project_dir", ""))) if getattr(brief, "_project_dir", "") else "No template selected."
    template_instruction = _template_instruction(brief)
    chart_catalog = _read_chart_catalog()
    return f"""You are a presentation design strategist. Produce two files for a PPT deck.

## User Requirements (from Brief Assistant)
- Scenario: {brief.scenario}
- Audience: {brief.audience}
- Goal: {brief.goal}
- Tone: {brief.tone}
- Style: {brief.style}
- Page count: {brief.page_count}
- Language: {brief.language}
- Speaker notes: {'yes' if brief.include_speaker_notes else 'no'}
- Charts: {'yes' if brief.include_charts else 'no'}
- Images: {'yes' if brief.include_images else 'no'}
- Animations: {'yes' if getattr(brief, 'include_animations', False) else 'no'}
- Must include: {', '.join(brief.must_include) if brief.must_include else 'none'}
- Avoid: {', '.join(brief.avoid) if brief.avoid else 'none'}
- Template kind: {brief.template_kind or 'none'}
- Template id/path: {brief.template_id or brief.template_path or 'none'}
- User notes: {brief.user_notes or 'none'}
- Image direction: {image_direction}

## Template Context
{template_context[:9000]}

## Template Handling
{template_instruction}

## PPT Master Chart / Infographic Template Catalog
Use this original PPT Master visual library when a slide needs structured data, comparison, process, roadmap, architecture, matrix, KPI, timeline, or framework content.
Do not force charts onto every slide. When a page benefits from one, name the best chart_template key in the Page Outline and spec_lock.
{chart_catalog[:12000]}

## Source Content
{source_content[:8000]}

## Output Format
Respond with a JSON object containing two fields:

```json
{{
  "design_spec": "... full design_spec.md content ...",
  "spec_lock": "... full spec_lock.md content ..."
}}
```

### design_spec.md rules
- Canvas: PPT 16:9 (1280x720)
- Include sections: Project Info, Canvas Spec, Visual Theme (colors), Typography, Page Outline (one line per page with title + content summary)
- For each non-cover/non-ending page, decide whether it should use a PPT Master chart_template. If yes, write `chart_template=<key>` in the Page Outline line.
- If images are enabled, include an Image Plan section that names which pages need generated supporting illustrations and describes the image prompt intent for each page
- If animations are enabled, include an Animation Direction section with restrained PowerPoint-native motion guidance
- Choose colors and fonts appropriate for the style ({brief.style}) and audience ({brief.audience})
- The page outline MUST have exactly {brief.page_count} pages
- Page 1 is always a cover/title slide
- Last page is always an ending/thank-you slide
- Write in {brief.language}

### spec_lock.md rules
- Machine-readable key-value format
- Include: canvas, colors (bg, primary, accent, text, text_secondary, border), typography (font_family, body size, title size), page_rhythm (P01-P{str(brief.page_count).zfill(2)})
- Include image_generation: {'enabled' if brief.include_images else 'disabled'}
- Include animations: {'enabled' if getattr(brief, 'include_animations', False) else 'disabled'}
- Include chart_templates with P01-P{str(brief.page_count).zfill(2)} keys when any page uses a chart template
- If a template is selected, include template_kind and template_source
- Use exactly the colors from design_spec
- page_rhythm: assign anchor/dense/breathing rhythm tags to each page
- Do NOT include blockquote guidance comments — only data lines

Return ONLY the JSON object, no other text."""


def _template_instruction(brief: Brief) -> str:
    kind = (brief.template_kind or "").strip()
    if not kind:
        return "Free design. Do not invent template constraints; use the user's style/tone as the primary direction."
    if kind == "brand":
        return (
            "Brand template selected. Treat template colors, logos, typography, and brand assets as locked identity. "
            "You may freely choose page structure as long as it stays on-brand."
        )
    if kind == "layout":
        return (
            "Layout template selected. Treat template SVG structure and page layout patterns as the main constraint. "
            "Adapt content into those structures while choosing deck identity from the user's brief."
        )
    if kind == "deck":
        return (
            "Deck template selected. Treat template identity, layout system, and reusable page roles as locked. "
            "Only adapt audience, outline, tone tweaks, and slide-specific content."
        )
    return "Template selected. Preserve its strongest visual constraints while adapting content."


def _call_llm(prompt: str) -> str:
    return chat_completion(
        system="You are a presentation design strategist. Always respond with valid JSON only.",
        user=prompt,
        temperature=0.7,
        max_tokens=8000,
        profile="strategy",
    )


def _parse_strategist_output(raw: str) -> dict[str, str]:
    """Extract design_spec and spec_lock from LLM JSON response."""
    raw = raw.strip()
    if raw.startswith("```json"):
        raw = raw[7:]
    if raw.startswith("```"):
        raw = raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    decoder = json.JSONDecoder()
    data = None
    for match in re.finditer(r"\{", raw):
        try:
            candidate, _ = decoder.raw_decode(raw[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            data = candidate
            break
    if data is None:
        raise ValueError("LLM response missing a JSON object")
    design_spec = _first_value(data, ["design_spec", "design_spec.md", "designSpec", "designSpecMd", "design_spec_md"])
    spec_lock = _first_value(data, ["spec_lock", "spec_lock.md", "specLock", "specLockMd", "spec_lock_md"])
    if not design_spec or not spec_lock:
        raise ValueError("LLM response missing design_spec or spec_lock field")
    return {"design_spec": str(design_spec), "spec_lock": str(spec_lock)}


def _first_value(data: dict, keys: list[str]) -> object:
    for key in keys:
        if key in data and data[key]:
            return data[key]
    lower_map = {str(key).lower().replace("-", "_"): value for key, value in data.items()}
    for key in keys:
        value = lower_map.get(key.lower().replace("-", "_"))
        if value:
            return value
    return ""


def run_strategist(brief: Brief, source_md_path: str | Path, project_dir: str | Path) -> dict[str, Path]:
    """Run the Strategist phase: brief + source -> design_spec.md + spec_lock.md.

    Returns dict with paths to the generated files.
    """
    project_dir = Path(project_dir)
    source_content = Path(source_md_path).read_text(encoding="utf-8")
    setattr(brief, "_project_dir", str(project_dir))

    prompt = _build_strategist_prompt(brief, source_content)
    raw_response = _call_llm(prompt)
    (project_dir / "strategist_raw_response.txt").write_text(raw_response, encoding="utf-8")
    result = _parse_strategist_output(raw_response)

    design_spec_path = project_dir / "design_spec.md"
    spec_lock_path = project_dir / "spec_lock.md"

    design_spec_path.write_text(result["design_spec"], encoding="utf-8")
    spec_lock_path.write_text(result["spec_lock"], encoding="utf-8")

    return {"design_spec": design_spec_path, "spec_lock": spec_lock_path}
