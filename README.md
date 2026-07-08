# ComfyUI Content Pipeline

A FastAPI control panel for **batch AI image and video generation** against a
remote **ComfyUI** backend, with GPU orchestration, cloud bridges and live
progress over WebSockets.

The pipeline models work as **lines**, each moving through six stages:

1. **Faceswap** — swap a reference face onto a body image.
2. **Clean** — remove logos / text / watermarks from the result.
3. **Set reference** — promote a clean image to the line's reference.
4. **Photo variations** — batch-generate variations (clothing color, hair
   shade, outfit, brand text, props, interior) from the reference.
5. **Video generation** — image-to-video (Wan 2.2) on a Vast.ai GPU, one clip
   per accepted photo.
6. **Drive upload** — push accepted photos and videos to a Google Drive folder.

## Highlights

- **GPU serialization guard** (`gpu_guard.py`) — every GPU op takes a
  process-wide `asyncio.Lock`, then `ensure_gpu_free()` checks the remote GPU
  over SSH: temperature and VRAM limits, model unloading, cool-down waits, and a
  ComfyUI restart as a last resort. Prevents OOM and thermal issues on a shared
  card.
- **Gemini-vision auto-prompting** (`pipeline.describe_video_prompt`) — turns a
  reference photo into an image-to-video prompt automatically, with multi-key
  rotation on rate limits and a safe default fallback so the user is never
  blocked.
- **Vast.ai bridge** (`vast_bridge.py`) — lists/starts/stops instances, checks
  the SSH tunnel, patches the i2v workflow, and streams generation progress.
- **Google Drive bridge** (`drive_bridge.py`, `gdrive_upload.py`) — resumable
  uploads with size verification and retry.
- **WebSocket progress** — faceswap, clean, batch photo/video generation and
  Drive upload all stream structured progress events to the browser.
- **SQLite state** (`database.py`) — lines, pipeline steps, photos, videos and
  Drive uploads, with per-item QA (accept/reject) status.

## Architecture

```
server.py            FastAPI app: REST + WebSocket endpoints, static hosting
pipeline.py          Core image ops (faceswap/clean/variations), Gemini prompting
gpu_guard.py         Remote GPU safety + serialization
vast_bridge.py       Vast.ai lifecycle + i2v video generation
drive_bridge.py      Google Drive delivery wrapper
gdrive_upload.py     Resumable, verified Drive uploads (OAuth)
database.py          SQLite schema + helpers
brand_words.py       Neutral word pool for text-print variations
backends/
  vast_helper.py     Vast.ai REST client
  comfy_client.py    ComfyUI REST/WS client
static/              Vanilla-JS + Tailwind UI (dashboard, line view, settings)
base_prompt*.json    ComfyUI API-format workflows for image ops
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # then fill in the values
uvicorn server:app --host 0.0.0.0 --port 8687
```

Open `http://localhost:8687`.

## Configuration

All endpoints, API keys and machine-specific paths come from the environment —
see `.env.example`. No secrets are committed; the SQLite DB, uploads and results
are git-ignored.

---

*Portfolio note: this repository is a sanitized extract of a private project.
Generated media and the runtime database are excluded.*
