# PPT Generation Pipeline Specification

## Purpose

Define the backend worker pipeline that converts a confirmed task brief and uploaded source material into a downloadable editable PPTX draft.

## Pipeline Overview

```text
1. Prepare workspace
2. Extract source content
3. Build normalized content model
4. Generate deck outline
5. Generate slide specs
6. Render PPTX
7. Validate PPTX
8. Upload result
9. Update task status
```

## Step 1: Prepare Workspace

Input:

- `task_id`
- `task_brief.json`
- uploaded source file keys

Output:

- isolated local workspace
- downloaded source files
- initialized logs

Failure examples:

- Missing source file
- Unsupported file type
- Download failure

## Step 2: Extract Source Content

Supported formats for MVP:

- PDF
- DOCX
- Markdown
- TXT
- pasted text

Output file:

```text
artifacts/extracted_content.json
```

Minimum schema:

```json
{
  "title": "string",
  "language": "zh-CN",
  "sections": [
    {
      "heading": "string",
      "text": "string",
      "tables": [],
      "images": []
    }
  ],
  "warnings": []
}
```

## Step 3: Generate Deck Outline

Input:

- `task_brief.json`
- `extracted_content.json`

Output:

```text
artifacts/deck_outline.json
```

Minimum schema:

```json
{
  "deck_title": "string",
  "scenario": "executive_report",
  "style": "executive_dark",
  "slides": [
    {
      "slide_number": 1,
      "title": "string",
      "purpose": "string",
      "key_message": "string",
      "source_refs": [],
      "visual_intent": "title_slide"
    }
  ]
}
```

Rules:

- Page count must match the confirmed brief unless `auto`.
- Slides should avoid dense paragraphs.
- Every slide should have one clear message.

## Step 4: Generate Slide Specs

Input:

- `deck_outline.json`
- `extracted_content.json`

Output:

```text
artifacts/slide_specs/slide_001.json
```

Minimum schema:

```json
{
  "slide_number": 1,
  "layout": "title_with_subtitle",
  "title": "string",
  "subtitle": "string",
  "blocks": [
    {
      "type": "text",
      "role": "key_point",
      "content": "string"
    }
  ],
  "speaker_notes": "string",
  "style_hints": {
    "density": "medium",
    "emphasis": "high"
  }
}
```

## Step 5: Render PPTX

Input:

- slide specs
- selected theme/style

Output:

```text
output/result.pptx
artifacts/render_manifest.json
```

Rendering should use PPT Master's existing DrawingML/PPTX generation capabilities where possible.

Current proven renderer entrypoint:

```bash
python skills/ppt-master/scripts/svg_to_pptx.py \
  <project_dir> \
  -s output \
  -o <output.pptx> \
  --only native
```

Important implementation notes:

- Use the wrapper script `skills/ppt-master/scripts/svg_to_pptx.py`, not the internal `svg_to_pptx/pptx_cli.py` module directly.
- The renderer reads generated SVG files from `<project_dir>/svg_output/` when `-s output` is passed.
- Native mode emits editable DrawingML shapes and can embed speaker notes from `<project_dir>/notes/`.
- The renderer requires Python 3.10+ and dependencies including `python-pptx`, `svglib`, `reportlab`, `Pillow`, and `lxml`.
- A local feasibility run generated an 11-slide native PPTX with 11 notes pages from `examples/ppt169_pritzker_2026` without using an IDE agent.

## Step 6: Validate PPTX

Validation checks:

- File exists and is non-empty.
- File can be opened as a ZIP/PPTX package.
- Required PPTX package parts exist.
- Slide count matches expected count.
- No missing referenced media.
- Optional later check: text overflow and basic layout sanity.

Output:

```text
artifacts/validation_report.json
```

Example:

```json
{
  "status": "passed",
  "slide_count": 12,
  "checks": [
    {"name": "pptx_zip_open", "status": "passed"},
    {"name": "slide_count", "status": "passed"}
  ],
  "warnings": []
}
```

## Step 7: Upload Result

Output:

- uploaded PPTX object key
- optional signed URL
- completed task row

## Retry Policy

- Extraction failures: no automatic retry unless storage/network related.
- LLM JSON parse failures: retry once with stricter repair prompt.
- Rendering failures: retry once after validating slide specs.
- Validation failures: retry render once if recoverable.

## MVP Quality Bar

A task is successful if:

- It generates a valid downloadable PPTX.
- The PPTX contains the requested number of slides.
- The slides are editable PowerPoint elements.
- The deck roughly follows the selected scenario and style.
- The output is good enough as a human-editable first draft.
