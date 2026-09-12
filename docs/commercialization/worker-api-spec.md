# Worker API Spec

## Purpose

This document defines the backend task contract for the PPT Master commercial generator. The current implementation supports a local JSON-backed worker prototype and an optional Postgres-backed queue contract.

## Task Lifecycle

```text
queued
running
generating
validating
completed
failed
cancelled
```

`completed`, `failed`, and `cancelled` are terminal states.

## Local Prototype Command

```bash
CLOUD_GENERATOR_MOCK=1 \
PYTHONPATH=skills/ppt-master/scripts \
/tmp/ppt-master-venv-312/bin/python -m cloud_generator.job_runner \
  --brief docs/commercialization/sample-brief.json \
  --source docs/commercialization/sample-source.md \
  --jobs-dir /tmp/ppt-master-jobs \
  --job-id demo_job \
  --max-attempts 2
```

The command writes:

```text
/tmp/ppt-master-jobs/demo_job/
  job_status.json
  design_spec.md
  spec_lock.md
  generation_report.json
  output/result.pptx
  notes/
  sources/
  svg_output/
```

## Job Status Shape

```json
{
  "job_id": "job_abc123",
  "status": "completed",
  "created_at": "2026-06-01T00:00:00+00:00",
  "updated_at": "2026-06-01T00:01:00+00:00",
  "brief_path": "/path/to/brief.json",
  "source_path": "/path/to/source.md",
  "output_path": "/tmp/ppt-master-jobs/job_abc123/output/result.pptx",
  "project_dir": "/tmp/ppt-master-jobs/job_abc123",
  "error_code": "",
  "error_message": "",
  "events": []
}
```

## API Endpoints

The local prototype is implemented by:

```bash
CLOUD_GENERATOR_MOCK=1 \
PYTHONPATH=skills/ppt-master/scripts \
/tmp/ppt-master-venv-312/bin/python -m cloud_generator.api_server \
  --host 127.0.0.1 \
  --port 8765 \
  --store json \
  --jobs-dir /tmp/ppt-master-api-jobs
```

For Postgres-backed task storage:

```bash
export DATABASE_URL="postgresql://postgres:postgres@localhost:5432/ppt_master"
export PPT_MASTER_ROUTE_SESSION_STORE=postgres
PYTHONPATH=skills/ppt-master/scripts \
/tmp/ppt-master-venv-312/bin/python -m cloud_generator.api_server \
  --host 127.0.0.1 \
  --port 8765 \
  --store postgres \
  --jobs-dir /tmp/ppt-master-api-pg-jobs
```

### Create Task

`POST /api/generation-tasks`

Local prototype route: `POST /generation-tasks`.

Request:

```json
{
  "brief_path": "docs/commercialization/sample-brief.json",
  "source_path": "docs/commercialization/sample-source.md",
  "job_id": "api_demo",
  "max_attempts": 2,
  "run_async": true
}
```

Response:

```json
{
  "task_id": "task_123",
  "status": "queued"
}
```

### Get Task Status

`GET /api/generation-tasks/:task_id`

Local prototype route: `GET /generation-tasks/:task_id`.

Response:

```json
{
  "task_id": "task_123",
  "status": "generating",
  "progress": {
    "stage": "executor",
    "generated_pages": 3,
    "total_pages": 6
  },
  "download_url": null,
  "error_message": null
}
```

### Download Result

`GET /api/generation-tasks/:task_id/download`

Local prototype route: `GET /generation-tasks/:task_id/download`.

Response:

```json
{
  "download_url": "https://storage.example.com/signed-url"
}
```

### Native Route Sessions

Native PPTX routes use a staged session because the upstream workflow requires
inspection and explicit confirmation before mutation. The local prototype
routes are:

- `POST /route-sessions` with `route=create_template|fill_native_pptx|enhance_native_pptx`, `source_text`, and exactly one uploaded `.pptx` in `material_files`.
- `GET /route-sessions/:session_id` to read the prepared analysis artifacts and confirmation state.
- The session payload includes `progress.stage`, `progress.percent`, `progress.message`, and `progress.at`; clients may poll the session while preparation or confirmed mutation is running.
- `POST /route-sessions/:session_id/confirm` with `confirmed: true` plus the route-specific `fill_plan` or `enhancement_plan`.
- `POST /route-sessions/:session_id/publish` with `name`, optional `description`, structured `authoring` metadata (`primary_color`, `font_family`, `tags`), and an idempotency key for Create Template sessions.
- `GET /route-sessions/:session_id/authoring` and `GET /route-sessions/:session_id/authoring-file/:filename` expose the sanitized authoring bundle for Create Template.
- `POST /route-sessions/:session_id/authoring-edit` applies a bounded direct edit to one authoring SVG element (`text`, `fill`, `font-size`, `x`, `y`, `width`, `height`, and related presentation attributes); the edit is written to the authoring IR and is included by the mirror compiler on publish.
- `POST /route-sessions/:session_id/authoring-undo` restores the latest authoring SVG snapshot for a file; snapshots remain outside the published authoring bundle.
- `GET /route-sessions/:session_id/authoring-history?file=slide_01.svg` lists bounded version snapshots, and `POST /route-sessions/:session_id/authoring-restore` restores a selected snapshot while preserving the current file as a new snapshot.
- `GET /route-sessions/:session_id/download` followed by `/download-file` after a completed Fill/Enhance session.

Create Template stops at `awaiting_authoring` after producing the upstream
manifest/native-structure workspace and editable `authoring-svg` IR, then
materializes and publishes a durable reusable template package through the
publish endpoint. Fill Native stops at
`awaiting_confirmation` after slide-library analysis and plan scaffolding.
Enhance Native stops at the same gate after project initialization and plan
generation. Set `PPT_MASTER_ROUTE_SESSION_STORE=postgres` alongside
`DATABASE_URL` to persist the state record in the durable Postgres store; the
local route directory remains the working area for uploaded and generated
files. Native mutation/publish is quota-metered exactly once at the
mutation boundary, and confirmation/publish requests are idempotent. If the
worker dies during preparation, the next read reconstructs a safe confirmation
state from completed analysis artifacts or marks the session failed for retry.
No route mutates a source PPTX in place.

## Production Mapping

`job_status.json` maps to `generation_tasks`:

- `job_id` -> `id`
- `status` -> `status`
- `brief_path` -> `brief_json` or `brief_file_key`
- `source_path` -> `source_file_key`
- `output_path` -> `output_file_key`
- `error_code` -> `error_code`
- `error_message` -> `error_message`
- `created_at`, `updated_at` -> timestamps

`events[]` maps to `task_events`.

## Postgres Queue

Schema:

```text
skills/ppt-master/scripts/cloud_generator/postgres_schema.sql
```

Helper module:

```text
skills/ppt-master/scripts/cloud_generator/postgres_queue.py
```

Local compose file:

```text
docker-compose.postgres.yml
```

Start local Postgres:

```bash
docker compose -f docker-compose.postgres.yml up -d
```

Initialize the local database:

```bash
export DATABASE_URL="postgresql://postgres:postgres@localhost:5432/ppt_master"
PYTHONPATH=skills/ppt-master/scripts \
/tmp/ppt-master-venv-312/bin/python -m cloud_generator.postgres_queue init
```

Enqueue a task:

```bash
PYTHONPATH=skills/ppt-master/scripts \
/tmp/ppt-master-venv-312/bin/python -m cloud_generator.postgres_queue enqueue \
  --job-id pg_demo \
  --brief-path docs/commercialization/sample-brief.json \
  --source-path docs/commercialization/sample-source.md \
  --project-dir /tmp/ppt-master-pg-jobs/pg_demo \
  --output-path /tmp/ppt-master-pg-jobs/pg_demo/output/result.pptx
```

Claim the next task:

```bash
PYTHONPATH=skills/ppt-master/scripts \
/tmp/ppt-master-venv-312/bin/python -m cloud_generator.postgres_queue claim \
  --worker-id local-worker-1
```

The claim operation uses `FOR UPDATE SKIP LOCKED`, so multiple workers can safely compete for queued tasks.

Run one Postgres-backed worker task:

```bash
CLOUD_GENERATOR_MOCK=1 \
PYTHONPATH=skills/ppt-master/scripts \
/tmp/ppt-master-venv-312/bin/python -m cloud_generator.postgres_worker \
  --jobs-dir /tmp/ppt-master-pg-jobs \
  --worker-id local-worker-1 \
  --once
```

## Worker Rules

- Run one task in one isolated project directory.
- Do not expose provider API keys to frontend code.
- Keep `generation_report.json` for debugging and quality analytics.
- Upload only `result.pptx` to user-facing storage.
- Retain intermediate artifacts for a short internal debugging window.
- Treat renderer failure as retryable only when SVG validation can repair the page.
