# Pipeline Feasibility Findings

## Summary

The PPTX rendering stage is already suitable for backend worker automation. A backend server does not need VS Code, Cursor, Claude Code, or another GUI IDE to convert prepared SVG slides into an editable PowerPoint file.

The major unresolved work is upstream of rendering: replacing the current IDE-agent Strategist and Executor behavior with a deterministic service that can create the project artifacts the renderer expects.

## Verified Locally

Command:

```bash
/tmp/ppt-master-venv-312/bin/python \
  skills/ppt-master/scripts/svg_to_pptx.py \
  examples/ppt169_pritzker_2026 \
  -s output \
  -o /tmp/ppt-master-pritzker-test.pptx \
  --only native \
  --no-cache \
  --workers 1
```

Result:

- Generated `/tmp/ppt-master-pritzker-test.pptx`.
- Output size: about 15 MB.
- Slide count: 11.
- Notes page count: 11.
- Mode: native DrawingML shapes.
- No IDE agent was involved.

## Proven Automatable Stages

- Source file conversion scripts exist for PDF, DOCX, XLSX, PPTX, web pages, and Markdown.
- Project initialization exists through `skills/ppt-master/scripts/project_manager.py`.
- SVG quality checking exists through `skills/ppt-master/scripts/svg_quality_checker.py`.
- SVG finalization exists through `skills/ppt-master/scripts/finalize_svg.py`.
- PPTX rendering exists through `skills/ppt-master/scripts/svg_to_pptx.py`.

## Main Productization Gap

Current PPT Master relies on an IDE agent for two high-value creative phases:

1. **Strategist**: reads source material, asks/uses confirmations, writes `design_spec.md` and `spec_lock.md`.
2. **Executor**: sequentially authors each SVG page into `svg_output/` and writes speaker notes.

These phases are not yet a normal command-line program. The skill explicitly requires hand-authored SVG pages, sequential generation, and per-page `spec_lock.md` rereads. For a commercial backend, this needs to become a controlled service that uses LLM calls to emit validated artifacts.

## Recommended MVP Automation Shape

Do not try to reproduce the full flexible IDE workflow at first. Instead, build a narrow command-style generator:

```text
task_brief.json
source.md
  -> design_spec.md
  -> spec_lock.md
  -> svg_output/*.svg
  -> notes/*.md
  -> result.pptx
```

The first version should support only a few fixed scenarios, styles, and page counts. It should validate every artifact before moving to the next phase.

## Implementation Implication

The backend worker should call the existing renderer as-is. Engineering effort should focus on:

- Brief-to-outline generation.
- Outline-to-slide-spec generation.
- Slide-spec-to-SVG generation.
- Schema validation.
- SVG quality checks.
- Retry and repair prompts for invalid JSON or invalid SVG.

This makes the commercial MVP feasible without installing an IDE on the server.

