# PPT Master Local Public Preview

This guide exposes the local cloud generator to other people for short-lived
testing. It is not a production deployment.

## 1. Configure `.env`

Copy `.env.example` to `.env` if you have not already done so, then configure
your text and image model providers.

Recommended for shared testing:

```env
PUBLIC_HOST=1
FRONTEND_PORT=8000
API_PORT=8766
JOBS_DIR=/tmp/ppt-master-api-jobs
STORE=json
PPT_MASTER_ACCESS_TOKEN=change-me-to-a-long-random-string
```

Text model example:

```env
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-chat
DEEPSEEK_API_KEY=your-key
```

Image model examples:

```env
IMAGE_BACKEND=openai
IMAGE_MODEL=gpt-image-2
OPENAI_API_KEY=your-key
OPENAI_BASE_URL=https://api.openai.com/v1
```

```env
IMAGE_BACKEND=qwen
IMAGE_MODEL=qwen-image-2.0-pro
QWEN_API_KEY=your-key
```

## 2. Start local services

```bash
./scripts/start-local-public.sh
```

The script starts:

- Frontend static server on `FRONTEND_PORT`
- Cloud generator API on `API_PORT`

It prints a URL like:

```text
http://<your-ip>:8000/cloud-generator.html?lang=zh-CN&api=http://<your-ip>:8766&token=<token>
```

Share that URL with people on the same network.

## 3. Temporary public URL

For short-lived external testing, tunnel the frontend and API ports with a tool
such as `cloudflared`, `ngrok`, or `frp`.

If your frontend and API use different tunnel URLs, open:

```text
https://frontend.example/cloud-generator.html?lang=zh-CN&api=https://api.example&token=<token>
```

If you reverse-proxy the API under the same domain at `/api`, the frontend will
use `/api` automatically when no `api=` query parameter is present.

## Safety Notes

- Always set `PPT_MASTER_ACCESS_TOKEN` before sharing a URL.
- Keep `STORE=json` and one local API process for a 2-core machine.
- Start with 8-12 slides and files under 30 MB.
- Stop the script with `Ctrl+C` when testing is done.
- Delete old job files in `JOBS_DIR` periodically.
