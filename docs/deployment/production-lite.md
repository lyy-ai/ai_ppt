# PPT Master Cloud Generator Lite Deployment

This is the recommended path for a small private beta on a 2-core lightweight server.

## Target Shape

- Static frontend served by Nginx or any static file server.
- `cloud_generator.api_server` behind `/api`.
- `STORE=json` for very small private tests, or `STORE=postgres` when multiple workers or restarts matter.
- Optional S3-compatible artifact storage for generated PPTX, SVG previews, and narration audio.
- One worker/API process on a 2-core server until usage proves otherwise.

## Remote Server Access

Keep deploy credentials outside this repository. Store SSH keys, passwords, and
server-only environment files in a password manager or an ignored local folder
such as `deploy/private/`.

Recommended local SSH aliases:

```sshconfig
# ~/.ssh/config or deploy/ssh_config.local
Host bestppt-backend
  HostName 36.212.51.4
  User root
  ServerAliveInterval 60
  ServerAliveCountMax 3
  IdentityFile ~/.ssh/bestppt_backend
  IdentitiesOnly yes

Host bestppt-gateway
  HostName 43.130.35.154
  User ubuntu
  ServerAliveInterval 60
  ServerAliveCountMax 3
  IdentityFile ~/.ssh/bestppt_gateway
  IdentitiesOnly yes
```

Connection checks:

```bash
ssh bestppt-backend 'hostname && systemctl status ppt-master-api --no-pager'
ssh bestppt-gateway 'hostname && systemctl status caddy --no-pager'
```

Current server roles:

- `bestppt-backend`: high-performance backend host. Keep API code, virtualenv,
  jobs, logs, and `.env.server` under `/data/liyangyang/ppt_project/`.
- `bestppt-gateway`: lightweight public gateway. Serve static frontend from
  `/var/www/ppt-master` and use Caddy to terminate HTTPS for
  `ppt.aigcstory.site`, `ppt-cn.aigcstory.site`, and `api.aigcstory.site`.

Do not SSH through the public product domains. Use server IPs or SSH aliases for
operations, and keep the domains for browser/API traffic.

Suggested sync commands:

```bash
rsync -az --delete \
  --exclude '.git' \
  --exclude 'node_modules' \
  --exclude '__pycache__' \
  --exclude '.env*' \
  --exclude 'jobs' \
  ./ bestppt-backend:/data/liyangyang/ppt_project/ppt-master/

rsync -az \
  index.html cloud-generator.html viewer.html assets docs \
  bestppt-gateway:/var/www/ppt-master/
```

After syncing backend code:

```bash
ssh bestppt-backend 'cd /data/liyangyang/ppt_project/ppt-master && python3 -m py_compile skills/ppt-master/scripts/cloud_generator/api_server.py'
ssh bestppt-backend 'systemctl restart ppt-master-api && systemctl status ppt-master-api --no-pager'
```

After syncing frontend files:

```bash
ssh bestppt-gateway 'caddy validate --config /etc/caddy/Caddyfile && systemctl reload caddy'
curl -I https://ppt.aigcstory.site/cloud-generator.html
curl -I https://ppt-cn.aigcstory.site/cloud-generator.html
```

Never commit:

- SSH private keys or `deploy/ssh_config.local`.
- `.env.server`, API keys, model keys, payment keys, or CloudBase credentials.
- Server job folders, generated PPTX files, user avatars, and auth stores.

## Minimum Environment

```env
PUBLIC_HOST=1
FRONTEND_PORT=8000
API_PORT=8766
STORE=json
JOBS_DIR=/var/lib/ppt-master/jobs
PPT_MASTER_ACCESS_TOKEN=<long-random-token>
PPT_MASTER_REQUIRE_AUTH=1
PPT_MASTER_SESSION_TTL_SECONDS=2592000

OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=<your-default-model>
DEEPSEEK_API_KEY=<your-key>

LLM_MODEL_BRIEF=<fast-chat-model>
LLM_MODEL_STRATEGY=<planning-model>
LLM_MODEL_SLIDE=<cost-effective-slide-model>
LLM_MODEL_SLIDE_FALLBACK=<stronger-slide-model>

# Optional upload scanning and cost telemetry
PPT_MASTER_CLAMSCAN_BIN=/usr/bin/clamscan
PPT_MASTER_COST_PER_PAGE_ATTEMPT_CENTS=0.02
PPT_MASTER_COST_PER_IMAGE_CENTS=1.00
PPT_MASTER_COST_PER_AUDIO_CENTS=0.20
```

## Optional Tencent COS / R2 / S3 Storage

Install optional storage dependency:

```bash
pip install boto3
```

Configure S3-compatible storage:

```env
PPT_MASTER_STORAGE_MODE=s3
S3_BUCKET=ppt-master-artifacts
S3_PREFIX=ppt-master/artifacts
S3_ENDPOINT_URL=https://cos.ap-guangzhou.myqcloud.com
S3_REGION=ap-guangzhou
S3_ACCESS_KEY_ID=<secret-id-or-access-key>
S3_SECRET_ACCESS_KEY=<secret-key>
S3_PRESIGN_EXPIRES=3600
PPT_MASTER_ARTIFACT_RETENTION_DAYS=14
```

For Cloudflare R2, use the R2 endpoint and `S3_REGION=auto`.

If the bucket is public or fronted by a CDN:

```env
S3_PUBLIC_BASE_URL=https://cdn.example.com
```

The API will continue to return the same artifact manifest shape:

```text
GET /generation-tasks/{task_id}/artifacts
```

To review or apply remote artifact cleanup:

```bash
./scripts/cleanup-cloud-generator-artifacts.py --days 14
./scripts/cleanup-cloud-generator-artifacts.py --days 14 --apply
```

## Local Service Command

Before starting a public/private-beta service, run the readiness checker:

```bash
python3 scripts/check-cloud-generator-readiness.py
```

Set `PPT_MASTER_DEPLOYMENT=production` in the server-only environment for a
strict release gate. In that profile every otherwise-acceptable private-beta
warning (local storage, JSON route sessions, missing scanner, mock payment,
or local email outbox) becomes a failure instead of allowing a public start.

It checks auth token strength, auth requirement, S3/COS config, optional ClamAV
scanner, unit-cost telemetry, and LLM credentials.

The running API also exposes non-secret provider status for frontend/ops checks:

```text
GET /provider-status
```

For no-cost regression coverage before sharing a URL, run:

```bash
./scripts/cloud-generator-smoke.py
./scripts/cloud-generator-regression-matrix.py
```

```bash
PYTHONPATH=skills/ppt-master/scripts \
python3 -m cloud_generator.api_server \
  --host 0.0.0.0 \
  --port 8766 \
  --store json \
  --jobs-dir /var/lib/ppt-master/jobs \
  --access-token "$PPT_MASTER_ACCESS_TOKEN" \
  --frontend-base-url https://your-domain.example
```

## Nginx Sketch

```nginx
server {
  listen 443 ssl;
  server_name your-domain.example;

  root /opt/ppt-master/ppt-master;
  index index.html;

  location / {
    try_files $uri $uri/ /index.html;
  }

  location /api/ {
    proxy_pass http://127.0.0.1:8766/;
    proxy_read_timeout 600s;
    proxy_send_timeout 600s;
    client_max_body_size 80m;
  }
}
```

When the workbench is served from the same domain, it will auto-use `/api` if no `api=` query parameter is provided.

## Smoke Test

Run the no-cost regression test before sharing the site:

```bash
scripts/cloud-generator-smoke.py
```

It verifies auth/quota, task ownership, generation, artifact manifest, retry, and source-material safety using `CLOUD_GENERATOR_MOCK=1`.

## Local Artifact Cleanup

Preview/private-beta runs can leave large local job folders. Check what would
be removed:

```bash
scripts/cleanup-cloud-generator-jobs.py \
  --jobs-dir /var/lib/ppt-master/jobs \
  --older-than-days 7
```

Delete completed jobs older than 7 days:

```bash
scripts/cleanup-cloud-generator-jobs.py \
  --jobs-dir /var/lib/ppt-master/jobs \
  --older-than-days 7 \
  --delete
```

Add `--include-failed` only when failed/cancelled task workspaces are no longer useful for debugging.

## 2-Core Server Guidance

- Keep concurrency at one API/worker process.
- Start with 4-8 page decks for external testers.
- Keep file uploads under 30 MB and total upload under 60 MB.
- Disable image/audio generation by default for broad tests; enable per trusted tester.
- Prefer S3/COS storage before inviting users outside your own machine, so generated artifacts are not tied to ephemeral local paths.
- Rotate `PPT_MASTER_ACCESS_TOKEN` after public tunnel tests.
