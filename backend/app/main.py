from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from app.ergonomics import analyze_pose, build_calibration_profile
from app.schemas import AnalyzeRequest, CalibrationProfile, CalibrationRequest
from app.storage import get_recent_events, init_db, insert_risk_event

app = FastAPI(title="ErgoPilot Prototype API", version="0.1.0")

# In prototype mode we allow localhost browser origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

calibration_profiles: dict[str, CalibrationProfile] = {}


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/calibrate")
def calibrate(payload: CalibrationRequest) -> dict[str, object]:
    profile = build_calibration_profile(payload.landmarks)
    calibration_profiles[payload.worker_id] = profile
    return {
        "worker_id": payload.worker_id,
        "calibration": profile.model_dump(),
        "message": "Calibration saved for worker.",
    }


@app.post("/api/analyze")
def analyze(payload: AnalyzeRequest) -> dict[str, object]:
    profile = calibration_profiles.get(payload.worker_id)
    result = analyze_pose(
        worker_id=payload.worker_id,
        landmarks=payload.landmarks,
        load_kg=payload.load_kg,
        frequency_lifts_per_min=payload.frequency_lifts_per_min,
        calibration=profile,
    )

    if result.risk_level in {"warning", "danger"}:
        # Persist only skeletal points and derived risk metadata.
        insert_risk_event(
            worker_id=result.worker_id,
            risk_level=result.risk_level,
            rula_score=result.rula_score,
            reba_score=result.reba_score,
            rwl_kg=result.rwl_kg,
            niosh_ratio=result.niosh_ratio,
            landmarks_payload=[lm.model_dump() for lm in payload.landmarks],
        )

    return result.model_dump()


@app.get("/api/events")
def events(limit: int = Query(default=50, ge=1, le=500)) -> dict[str, object]:
    return {"count": limit, "items": get_recent_events(limit)}
