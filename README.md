# ErgoPilot Prototype

Prototype implementation for a privacy-first ergonomic feedback system.

## Architecture

- `backend/` - FastAPI service for ergonomic scoring + local SQLite audit storage.
- `frontend/` - Browser client using MediaPipe Pose + color HUD + IndexedDB.

### Privacy model

- Raw video frames are processed live in browser only.
- Persisted records contain only skeletal landmarks and derived risk scores.
- Risk clips are stored as anonymized context videos in browser IndexedDB (`risk_clips`): the worker region is blurred/masked, surrounding scene remains visible, and the skeleton wireframe is overlaid.
- No cloud storage is required for this prototype.

## Backend setup

```bash
cd /Users/sriyanm/Courses/EECS497/project/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

## Frontend setup

Use any static file server so camera APIs work under `http://localhost`.

```bash
cd /Users/sriyanm/Courses/EECS497/project/frontend
python3 -m http.server 5173
```

Open: `http://localhost:5173`

## API endpoints

- `GET /health` - service health.
- `POST /api/auth/signup` - creates a user account and returns a bearer token.
- `POST /api/auth/login` - authenticates with email/password and returns a bearer token.
- `GET /api/auth/me` - returns current user profile from bearer token.
- `POST /api/admin/reset-demo-users` - local maintenance endpoint: removes non-demo users and resets demo credentials.
- `POST /api/calibrate` - saves neutral posture profile for a worker.
- `POST /api/analyze` - computes RULA/REBA/NIOSH proxy scores, returns risk level.
- `GET /api/events?limit=50` - recent high-risk skeletal events from local SQLite.

### Auth request/response quick reference

- Signup request body:
  - `email` (valid email)
  - `password` (8-256 chars)
  - `display_name` (1-120 chars)
- Login request body:
  - `email`
  - `password`
- Signup/login response body:
  - `access_token`
  - `token_type` (`bearer`)
- Local demo account remains available: `demo@ergopilot.local` / `changeme`.

### Single-account-per-device mode

- The browser stores a local account lock (`locked_account_email`) after first successful sign-in/signup.
- Signup is still available, but only for the same locked email on that browser/device.
- Attempting a different account on the same browser is blocked in UI.
- Recording and dashboard now use an account-derived `worker_id` to avoid mixing clip/event data across accounts.

### Reset users to demo-only (local maintenance)

- Endpoint: `POST /api/admin/reset-demo-users`
- Required auth:
  - valid bearer token in `Authorization`
  - header `X-ErgoPilot-Admin-Reset: demo-only`
- Effect:
  - deletes all non-demo users
  - ensures demo account exists and resets demo credentials to `demo@ergopilot.local` / `changeme`

## Notes

- Scoring logic is a prototype approximation designed for rapid validation.
- For production-grade scores, replace heuristic mappings with full RULA/REBA/NIOSH worksheets and validated calibration pipelines.
- Clip capture is browser-dependent (`MediaRecorder`). If unsupported, the app still stores skeletal risk events.

## Local LLM setup (Ollama)

The dashboard **Areas for Improvement** summary can call a local Ollama model through the backend (`POST /api/improvement-summary`).

1) Start Ollama

```bash
ollama serve
```

2) Pull a model (example)

```bash
ollama pull llama3.2:3b
```

3) Configure backend (optional env vars)

- `OLLAMA_BASE_URL` (default: `http://localhost:11434`)
- `OLLAMA_MODEL` (default: `llama3.2:3b`)
- `OLLAMA_TIMEOUT_SECONDS` (default: `8`)
- `OLLAMA_MAX_RETRIES` (default: `2`)
- `OLLAMA_ENABLED` (default: `1`; set `0` to disable LLM summarization)

If Ollama is unavailable, the app automatically falls back to the original rule-based frequency summary.

## Quantitative CV benchmark (COCO)

Best first benchmark for this prototype: evaluate MediaPipe keypoint accuracy on an online public dataset (COCO val2017) before ergonomic score-level validation.

### 1) Install benchmark dependencies

```bash
cd /Users/sriyanm/Courses/EECS497/project/backend
source .venv/bin/activate
pip install -r requirements-benchmark.txt
```

### 2) Run a quick benchmark (downloads COCO automatically)

```bash
cd /Users/sriyanm/Courses/EECS497/project
python3 backend/benchmark_pose_coco.py \
  --download \
  --num-instances 300
```

Notes:
- First run downloads COCO annotations + `val2017` images to `backend/data/coco` (large download).
- Results are saved to `backend/benchmark_results/coco_pose_metrics.json`.

### 3) Run a more stable benchmark

```bash
cd /Users/sriyanm/Courses/EECS497/project
python3 backend/benchmark_pose_coco.py \
  --num-instances 1000 \
  --model-complexity 2 \
  --out-json backend/benchmark_results/coco_pose_metrics_1000.json
```

### 4) Interpret output

- `mean_norm_error`: lower is better.
- `pck@0.05` / `pck@0.10`: higher is better.
- `per_joint`: use this to find weak joints (often wrists/ankles in occluded views).

### 5) Use this as a regression gate

After any CV change, rerun with the same seed and compare `pck@0.10` and `mean_norm_error`.
Reject changes that reduce `pck@0.10` or increase error without clear latency gains.
