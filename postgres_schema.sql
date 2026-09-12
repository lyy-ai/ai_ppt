-- PPT Master Cloud Generator Postgres queue schema.
-- Designed for local MVP and production-compatible migration.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'generation_task_status') THEN
    CREATE TYPE generation_task_status AS ENUM (
      'queued',
      'running',
      'generating',
      'validating',
      'completed',
      'failed',
      'cancelled'
    );
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS generation_tasks (
  id text PRIMARY KEY DEFAULT ('job_' || encode(gen_random_bytes(6), 'hex')),
  status generation_task_status NOT NULL DEFAULT 'queued',
  priority integer NOT NULL DEFAULT 100,
  attempts integer NOT NULL DEFAULT 0,
  max_attempts integer NOT NULL DEFAULT 2,
  owner_user_id text NOT NULL DEFAULT '',
  quota_debited boolean NOT NULL DEFAULT false,

  brief_json jsonb,
  brief_path text,
  source_text text,
  source_path text,

  project_dir text,
  output_path text,
  generation_report_path text,

  error_code text,
  error_message text,
  locked_by text,
  locked_at timestamptz,

  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz,
  completed_at timestamptz
);

ALTER TABLE generation_tasks
  ADD COLUMN IF NOT EXISTS owner_user_id text NOT NULL DEFAULT '';

ALTER TABLE generation_tasks
  ADD COLUMN IF NOT EXISTS quota_debited boolean NOT NULL DEFAULT false;

CREATE TABLE IF NOT EXISTS task_events (
  id bigserial PRIMARY KEY,
  task_id text NOT NULL REFERENCES generation_tasks(id) ON DELETE CASCADE,
  event_type text NOT NULL,
  message text NOT NULL,
  metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS generation_tasks_queue_idx
  ON generation_tasks (priority ASC, created_at ASC)
  WHERE status = 'queued';

CREATE INDEX IF NOT EXISTS generation_tasks_status_idx
  ON generation_tasks (status, updated_at DESC);

CREATE INDEX IF NOT EXISTS generation_tasks_owner_idx
  ON generation_tasks (owner_user_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS task_events_task_id_idx
  ON task_events (task_id, created_at ASC);

CREATE TABLE IF NOT EXISTS route_sessions (
  id text PRIMARY KEY,
  owner_user_id text NOT NULL DEFAULT '',
  route text NOT NULL,
  status text NOT NULL,
  state_json jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS route_sessions_owner_idx
  ON route_sessions (owner_user_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS route_sessions_status_idx
  ON route_sessions (status, updated_at DESC);

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS trigger AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS generation_tasks_set_updated_at ON generation_tasks;
CREATE TRIGGER generation_tasks_set_updated_at
BEFORE UPDATE ON generation_tasks
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS route_sessions_set_updated_at ON route_sessions;
CREATE TRIGGER route_sessions_set_updated_at
BEFORE UPDATE ON route_sessions
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();
