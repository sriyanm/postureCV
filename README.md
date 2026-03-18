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
- `POST /api/calibrate` - saves neutral posture profile for a worker.
- `POST /api/analyze` - computes RULA/REBA/NIOSH proxy scores, returns risk level.
- `GET /api/events?limit=50` - recent high-risk skeletal events from local SQLite.

## Notes

- Scoring logic is a prototype approximation designed for rapid validation.
- For production-grade scores, replace heuristic mappings with full RULA/REBA/NIOSH worksheets and validated calibration pipelines.
- Clip capture is browser-dependent (`MediaRecorder`). If unsupported, the app still stores skeletal risk events.
