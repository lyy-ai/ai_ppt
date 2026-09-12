# Commercial Cloud Generator Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a first commercial version of PPT Master where users can upload source material, clarify requirements through an AI Brief Assistant, and asynchronously download an editable PPTX draft.

**Architecture:** The MVP uses a commercial web frontend, a server-side brief assistant, a task API, object storage, a queue, and a Dockerized worker that runs a command-style PPT Master generation pipeline. The first release does not include online slide editing or multi-turn local slide revision.

**Tech Stack:** Existing static PPT Master site, future Next.js or API service, Postgres, object storage, queue, Python worker, PPT Master rendering scripts, server-side LLM API calls.

---

## Phase 0: Product Alignment

### Task 1: Confirm MVP Boundaries

**Files:**
- Read: `docs/commercialization/product-requirements.md`
- Read: `docs/commercialization/system-design.md`

**Step 1: Review product scope**

Confirm the MVP includes upload, AI brief, async generation, progress, history, and download.

**Step 2: Confirm explicit non-goals**

Confirm the MVP excludes online editing, local slide revision, template marketplace, and complex transition/image generation.

**Step 3: Record decisions**

If scope changes, update `docs/commercialization/product-requirements.md`.

## Phase 1: Commercial Landing Page

### Task 2: Redesign Static Homepage

**Files:**
- Modify: `index.html`
- Reference: `docs/commercialization/product-requirements.md`

**Step 1: Write visual/content acceptance checklist**

Expected checklist:

- Hero explains editable PPTX generation.
- Primary CTA is `Generate a PPT Draft`.
- Secondary CTA is `View Examples`.
- Page includes scenarios, workflow, examples, and trust/open-source section.
- Page does not pretend generation is instant.

**Step 2: Implement homepage sections**

Use PPT Master's own brand, examples, and editable-PPTX differentiator. Borrow the commercial structure from JJT, not its copy or assets.

**Step 3: Test static page locally**

Run a local static server from the repo root:

```bash
python3 -m http.server 8000
```

Open:

```text
http://localhost:8000/
```

Expected: homepage loads and remains responsive on desktop and mobile.

## Phase 2: Brief Assistant Prototype

### Task 3: Implement Brief Schema

**Files:**
- Create: `commercial/schemas/task_brief.schema.json`
- Reference: `docs/commercialization/brief-assistant-spec.md`

**Step 1: Encode controlled values**

Create a JSON Schema that includes scenario, audience, goal, style, page count, notes, charts, images, must_include, avoid, and source_summary.

**Step 2: Validate sample briefs**

Create valid and invalid sample JSON files.

Expected: valid samples pass and invalid samples fail.

### Task 4: Build Brief Assistant API Prototype

**Files:**
- Create: `commercial/brief_assistant/assistant.py` or equivalent service file
- Test: `commercial/tests/test_brief_assistant.py`

**Step 1: Write tests for missing-field behavior**

Expected:

- Given vague input, assistant asks audience.
- Given audience and goal, assistant asks page count or style.
- Given complete brief, assistant returns `ready_to_confirm = true`.

**Step 2: Implement deterministic scaffold first**

Before connecting a real model, implement a rule-based stub that returns the correct response shape.

**Step 3: Add LLM-backed implementation behind an interface**

Keep the output shape identical to the stub.

## Phase 3: Pipeline Contracts

### Task 5: Add Artifact Schemas

**Files:**
- Create: `commercial/schemas/extracted_content.schema.json`
- Create: `commercial/schemas/deck_outline.schema.json`
- Create: `commercial/schemas/slide_spec.schema.json`
- Create: `commercial/schemas/validation_report.schema.json`
- Reference: `docs/commercialization/pipeline-spec.md`

**Step 1: Define schemas**

Start with the minimum fields documented in the pipeline spec.

**Step 2: Add schema validation tests**

Expected: sample artifacts pass validation and malformed artifacts fail early.

### Task 6: Build Local Pipeline Skeleton

**Files:**
- Create: `commercial/pipeline/run_pipeline.py`
- Create: `commercial/pipeline/extract.py`
- Create: `commercial/pipeline/outline.py`
- Create: `commercial/pipeline/specs.py`
- Create: `commercial/pipeline/render.py`
- Create: `commercial/pipeline/validate.py`
- Test: `commercial/tests/test_pipeline_smoke.py`

**Step 1: Implement CLI skeleton**

Target command:

```bash
python -m commercial.pipeline.run_pipeline \
  --brief task_brief.json \
  --input sample.md \
  --workspace /tmp/ppt-master-task \
  --output result.pptx
```

**Step 2: Make every phase write an artifact**

Start with deterministic placeholder content.

**Step 3: Validate that the skeleton reaches completion**

Expected: all artifact files are present and a placeholder output file is produced.

## Phase 4: Real PPTX Rendering

### Task 7: Connect Existing PPT Master Rendering

**Files:**
- Inspect: `skills/ppt-master/`
- Modify: `commercial/pipeline/render.py`
- Test: `commercial/tests/test_render_pptx.py`

**Step 1: Use existing PPTX generation entrypoint**

Use:

```bash
python skills/ppt-master/scripts/svg_to_pptx.py \
  <project_dir> \
  -s output \
  -o <output.pptx> \
  --only native
```

This entrypoint has been locally verified with `examples/ppt169_pritzker_2026`, producing an 11-slide native PPTX with 11 notes pages.

**Step 2: Map slide specs to existing rendering inputs**

Keep the mapping narrow for MVP layouts. The immediate gap is not rendering; it is generating the project directory artifacts the renderer expects, especially `svg_output/*.svg` and `notes/*.md`.

**Step 3: Generate a valid PPTX from sample specs**

Expected: output opens as a PPTX package and slide count matches.

## Phase 5: Worker And Task API

### Task 8: Define Task Persistence

**Files:**
- Create: `commercial/db/schema.sql`
- Reference: `docs/commercialization/system-design.md`

**Step 1: Create tables**

Add users, generation_tasks, task_events, and brief_sessions.

**Step 2: Add migration notes**

Document how to run the schema locally.

### Task 9: Add Worker Job Runner

**Files:**
- Create: `commercial/worker/worker.py`
- Test: `commercial/tests/test_worker_job.py`

**Step 1: Implement local job execution**

Given a task id and local source path, run the pipeline and update status.

**Step 2: Add failure handling**

Expected: exceptions mark task as failed and write a task event.

## Phase 6: End-To-End MVP

### Task 10: Wire Upload To Generation

**Files:**
- To be decided after frontend/API stack selection.

**Step 1: Implement upload**

Store source files in object storage or local dev storage.

**Step 2: Create task**

Persist brief and enqueue job.

**Step 3: Poll task status**

Frontend shows queued, extracting, outlining, rendering, validating, completed, or failed.

**Step 4: Download result**

Completed tasks expose a download link.

## Phase 7: Verification

### Task 11: Run End-To-End Sample

**Files:**
- Create: `commercial/samples/executive_report.md`

**Step 1: Generate task brief**

Use a fixed valid brief.

**Step 2: Run pipeline**

Expected: PPTX generated and validation passes.

**Step 3: Open or inspect PPTX**

Expected: file is a valid PPTX and contains expected slide count.

### Task 12: Private Beta Checklist

**Files:**
- Read: `docs/commercialization/test-strategy.md`

**Step 1: Run tests**

Run all unit, contract, integration, and smoke tests.

**Step 2: Review cost metrics**

Record LLM calls, tokens, and runtime.

**Step 3: Review user messaging**

Confirm product states clearly say generation is asynchronous.

---

## Execution Recommendation

Start with Phase 2 and Phase 3 before the full backend. The key technical risk is not the landing page; it is whether PPT Master can be invoked as a deterministic command-style generation pipeline without an IDE agent.

After local feasibility testing, the rendering stage is no longer the highest risk: `skills/ppt-master/scripts/svg_to_pptx.py` can generate a valid native PPTX from an existing example project. The next implementation spike should focus on producing `svg_output/*.svg` and `notes/*.md` from a constrained `task_brief.json` and source Markdown.
