# Cloud Generator Runbook

## Purpose

This runbook describes how to run the first commercial backend prototype:

```text
brief.json + source.md
  -> design_spec.md
  -> spec_lock.md
  -> svg_output/*.svg
  -> notes/*.md
  -> result.pptx
```

The pipeline does not require VS Code, Cursor, Claude Code, or any GUI IDE on the server. It uses normal command-line Python plus an OpenAI-compatible LLM endpoint.

## Local Mock Run

Use mock mode when testing pipeline wiring without spending model tokens:

```bash
CLOUD_GENERATOR_MOCK=1 \
PYTHONPATH=skills/ppt-master/scripts \
/tmp/ppt-master-venv-312/bin/python -m cloud_generator.orchestrator \
  --brief docs/commercialization/sample-brief.json \
  --source docs/commercialization/sample-source.md \
  --project-dir /tmp/ppt-master-cloud-sample \
  --output /tmp/ppt-master-cloud-sample.pptx \
  --max-attempts 2 \
  --keep
```

Expected result:

- `design_spec.md` and `spec_lock.md` are written under the project directory.
- `svg_output/` contains one SVG per page.
- `notes/` contains one note file per page plus `total.md`.
- `generation_report.json` records every page attempt and retry.
- The final PPTX is written to the path passed through `--output`.

## DeepSeek Run

Set the key in your shell instead of committing it to the repository:

```bash
export DEEPSEEK_API_KEY="replace-with-local-secret"
export OPENAI_BASE_URL="https://api.deepseek.com"
export OPENAI_MODEL="deepseek-chat"

PYTHONPATH=skills/ppt-master/scripts \
/tmp/ppt-master-venv-312/bin/python -m cloud_generator.orchestrator \
  --brief docs/commercialization/sample-brief.json \
  --source docs/commercialization/sample-source.md \
  --project-dir /tmp/ppt-master-cloud-deepseek \
  --output /tmp/ppt-master-cloud-deepseek.pptx \
  --max-attempts 2 \
  --keep
```

## Ark Run

Ark uses an OpenAI-compatible endpoint, so the same client can call it:

```bash
export ARK_API_KEY="replace-with-local-secret"
export OPENAI_BASE_URL="https://ark.cn-beijing.volces.com/api/coding/v3"
export OPENAI_MODEL="replace-with-your-ark-model-or-endpoint-id"

PYTHONPATH=skills/ppt-master/scripts \
/tmp/ppt-master-venv-312/bin/python -m cloud_generator.orchestrator \
  --brief docs/commercialization/sample-brief.json \
  --source docs/commercialization/sample-source.md \
  --project-dir /tmp/ppt-master-cloud-ark \
  --output /tmp/ppt-master-cloud-ark.pptx \
  --max-attempts 2 \
  --keep
```

## Backend Worker Shape

In production, the web app should not call this as a long HTTP request. Use a job table and worker:

1. Frontend collects source material and structured brief.
2. API stores files and creates a `generation_jobs` row.
3. Worker runs the pipeline in an isolated project directory.
4. Worker uploads the generated PPTX to object storage.
5. API marks the job `completed` with a download URL.

## Local Worker Job Run

Use `job_runner` when testing the task lifecycle contract:

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

This writes `/tmp/ppt-master-jobs/demo_job/job_status.json` and places the generated deck at `/tmp/ppt-master-jobs/demo_job/output/result.pptx`.

## Local API Run

Start the dependency-free local API server:

```bash
CLOUD_GENERATOR_MOCK=1 \
PYTHONPATH=skills/ppt-master/scripts \
/tmp/ppt-master-venv-312/bin/python -m cloud_generator.api_server \
  --host 127.0.0.1 \
  --port 8765 \
  --store json \
  --jobs-dir /tmp/ppt-master-api-jobs
```

Create a task:

```bash
curl -s http://127.0.0.1:8765/generation-tasks \
  -H 'Content-Type: application/json' \
  -d '{
    "brief_path": "docs/commercialization/sample-brief.json",
    "source_path": "docs/commercialization/sample-source.md",
    "job_id": "api_demo",
    "max_attempts": 2,
    "run_async": true
  }'
```

Check status:

```bash
curl -s http://127.0.0.1:8765/generation-tasks/api_demo
```

Get the local result path:

```bash
curl -s http://127.0.0.1:8765/generation-tasks/api_demo/download
```

To use Postgres instead of the JSON job store:

```bash
export DATABASE_URL="postgresql://postgres:postgres@localhost:5432/ppt_master"
PYTHONPATH=skills/ppt-master/scripts \
/tmp/ppt-master-venv-312/bin/python -m cloud_generator.api_server \
  --host 127.0.0.1 \
  --port 8765 \
  --store postgres \
  --jobs-dir /tmp/ppt-master-api-pg-jobs
```

In Postgres mode, `POST /generation-tasks` only enqueues the task. Run `postgres_worker` to consume queued tasks.

## Optional Postgres Queue

JSON files are good for smoke tests. Postgres is better once we want local behavior to match production queue semantics.

Start local Postgres:

```bash
docker compose -f docker-compose.postgres.yml up -d
```

Check health:

```bash
docker compose -f docker-compose.postgres.yml ps
```

Install the optional queue dependency:

```bash
/tmp/ppt-master-venv-312/bin/python -m pip install 'psycopg[binary]>=3.1'
```

Initialize schema:

```bash
export DATABASE_URL="postgresql://postgres:postgres@localhost:5432/ppt_master"
PYTHONPATH=skills/ppt-master/scripts \
/tmp/ppt-master-venv-312/bin/python -m cloud_generator.postgres_queue init
```

Enqueue a local task:

```bash
PYTHONPATH=skills/ppt-master/scripts \
/tmp/ppt-master-venv-312/bin/python -m cloud_generator.postgres_queue enqueue \
  --job-id pg_demo \
  --brief-path docs/commercialization/sample-brief.json \
  --source-path docs/commercialization/sample-source.md \
  --project-dir /tmp/ppt-master-pg-jobs/pg_demo \
  --output-path /tmp/ppt-master-pg-jobs/pg_demo/output/result.pptx \
  --max-attempts 2
```

Claim a task:

```bash
PYTHONPATH=skills/ppt-master/scripts \
/tmp/ppt-master-venv-312/bin/python -m cloud_generator.postgres_queue claim \
  --worker-id local-worker-1
```

Run one queued task through the Postgres worker:

```bash
CLOUD_GENERATOR_MOCK=1 \
PYTHONPATH=skills/ppt-master/scripts \
/tmp/ppt-master-venv-312/bin/python -m cloud_generator.postgres_worker \
  --jobs-dir /tmp/ppt-master-pg-jobs \
  --worker-id local-worker-1 \
  --once
```

Verify completion:

```bash
PYTHONPATH=skills/ppt-master/scripts \
/tmp/ppt-master-venv-312/bin/python - <<'PY'
from cloud_generator.postgres_queue import get_task, list_events
task = get_task("pg_demo")
print(task["status"], task["output_path"], task["error_message"])
print([event["event_type"] for event in list_events("pg_demo")])
PY
```

Stop local Postgres:

```bash
docker compose -f docker-compose.postgres.yml down
```

## Current Guardrails

- The first commercial phase should generate an initial draft only.
- Local slide-by-slide editing is intentionally out of scope.
- Every generated SVG still needs validation before being accepted in production.
- Failed SVG generation should retry with the validation error as repair context.
- The renderer should run in a container with pinned Python dependencies.
- API keys must live in environment variables or a secret manager, never in source files.
