# PPT Master Cloud Generator System Design

## Goal

Build a commercial web product that wraps PPT Master's generation capability in a guided, asynchronous workflow. The system should let users upload material, clarify requirements through an AI assistant, generate a PPTX draft in a backend worker, and download the result.

## Non-Goal

This design does not attempt to build a JJT-style online slide editor. It intentionally excludes local slide edits, real-time co-editing, and full multi-turn PPT editing in the MVP.

## High-Level Architecture

```text
Browser
  -> Web App
  -> API Server
  -> Database
  -> Object Storage
  -> Queue
  -> Worker
  -> PPT Master Pipeline
  -> Object Storage
  -> Browser Download
```

## Recommended MVP Stack

- Frontend: Next.js or existing static site upgraded gradually.
- API: Next.js API routes, FastAPI, or a small Node service.
- Database: Postgres through Supabase or Neon.
- Object storage: Cloudflare R2, S3, or Supabase Storage.
- Queue: Postgres-backed queue for MVP; Redis/BullMQ, Inngest, or Trigger.dev can be introduced later if concurrency grows.
- Worker: Dockerized Python worker running PPT Master scripts.
- LLM access: Server-side only, never exposed from the browser.

## Core Components

### 1. Commercial Web Frontend

Responsibilities:

- Landing page.
- Upload UI.
- AI Brief Assistant chat UI.
- Brief confirmation page.
- Task progress page.
- Download/result page.
- Task history.

### 2. Brief Assistant API

The assistant is not the generator. It turns messy user intent into a structured task brief.

Responsibilities:

- Maintain a draft brief.
- Ask 3-5 targeted questions.
- Stop when required fields are complete.
- Return both user-facing reply and machine-readable `brief_draft`.

### 3. Task API

Responsibilities:

- Create generation tasks.
- Store task metadata.
- Enqueue worker jobs.
- Return task status.
- Return signed download URLs.

### 4. Worker

Responsibilities:

- Download source files.
- Extract content.
- Generate outline.
- Generate slide specs.
- Render PPTX.
- Validate PPTX.
- Upload output.
- Update task status and logs.

### 5. PPT Master Command Pipeline

Target command shape:

```bash
ppt-master generate \
  --brief task_brief.json \
  --input source.pdf \
  --workspace /tmp/ppt-master/tasks/task_123 \
  --output result.pptx
```

The command should not require an IDE or a human agent session.

Near-term implementation can wrap the existing renderer:

```bash
python skills/ppt-master/scripts/svg_to_pptx.py <project_dir> -s output -o result.pptx --only native
```

This confirms the final SVG-to-PPTX rendering stage can run inside a backend worker. The unresolved automation work is the earlier stage that turns uploaded source material and a confirmed brief into `design_spec.md`, `svg_output/*.svg`, `notes/*.md`, and related project artifacts.

## Data Flow

```text
User upload
  -> source file stored
  -> brief chat narrows requirements
  -> user confirms brief
  -> task row created
  -> queue job created
  -> worker processes task
  -> output PPTX stored
  -> user downloads result
```

## Task State Machine

```text
draft
uploaded
briefing
queued
extracting
outlining
spec_generating
rendering
validating
completed
failed
cancelled
```

Every transition should write a timestamp and a short log entry.

For the MVP, Postgres is the preferred local queue because it gives us the future production shape without adding Redis:

```sql
SELECT id
FROM generation_tasks
WHERE status = 'queued'
ORDER BY priority ASC, created_at ASC
FOR UPDATE SKIP LOCKED
LIMIT 1;
```

This is enough for one or more worker processes to claim tasks safely.

## Artifact Contract

Each task workspace should preserve:

```text
source/
  original_file
artifacts/
  extracted_content.json
  task_brief.json
  deck_outline.json
  slide_specs/
    slide_001.json
    slide_002.json
  render_manifest.json
  validation_report.json
output/
  result.pptx
logs/
  worker.log
```

## Minimum Database Tables

### users

- `id`
- `email`
- `created_at`

### generation_tasks

- `id`
- `user_id` (nullable during local prototype)
- `status`
- `priority`
- `attempts`
- `max_attempts`
- `scenario`
- `style`
- `page_count`
- `language`
- `brief_json`
- `source_file_key`
- `output_file_key`
- `project_dir`
- `locked_by`
- `locked_at`
- `error_code`
- `error_message`
- `created_at`
- `updated_at`
- `started_at`
- `completed_at`

### task_events

- `id`
- `task_id`
- `event_type`
- `message`
- `metadata_json`
- `created_at`

### brief_sessions

- `id`
- `user_id`
- `task_id`
- `brief_draft_json`
- `missing_fields_json`
- `created_at`
- `updated_at`

## Security Requirements

- Never expose LLM API keys to the browser.
- Use signed upload and download URLs.
- Restrict file size and accepted MIME types.
- Virus/malware scanning can be deferred, but file handling must be isolated.
- Worker jobs must run in isolated task directories.
- Do not execute user-provided scripts or macros.
- Delete temporary files after retention period.

## Cost Controls

- Limit free users by file size, page count, and task count.
- Limit LLM tokens per phase.
- Disable image generation by default in MVP.
- Retry failed tasks at most once automatically.
- Store intermediate artifacts to avoid repeating expensive phases.

## Operational Requirements

- Worker must be restartable.
- Queued jobs must be idempotent or safely resumable.
- Every failed task must include a user-safe error and an internal diagnostic.
- Admin should be able to inspect task artifacts for debugging.
