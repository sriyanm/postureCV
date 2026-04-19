import jwt
import time
from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth import create_access_token, decode_access_token, hash_password, verify_password
from app.ergonomics import analyze_pose, build_calibration_profile
from app.llm_summary import synthesize_improvement_summary
from app.schemas import (
    AnalyzeRequest,
    CalibrationProfile,
    CalibrationRequest,
    ImprovementSummaryRequest,
    ImprovementSummaryResponse,
    LoginRequest,
    SignupRequest,
    TokenResponse,
    UserPublic,
)
from app.storage import (
    UserAlreadyExistsError,
    clear_posture_samples,
    clear_risk_events,
    create_user,
    delete_posture_sample,
    delete_posture_samples_in_window,
    delete_risk_event,
    get_recent_events,
    get_session_averages,
    get_user_by_email,
    init_db,
    insert_posture_sample,
    insert_risk_event,
    seed_demo_user_if_empty,
    reset_users_to_demo,
)

app = FastAPI(title="ErgoPilot Prototype API", version="0.1.0")

bearer_scheme = HTTPBearer(auto_error=False)

# In prototype mode we allow localhost browser origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

calibration_profiles: dict[str, CalibrationProfile] = {}
risk_event_last_saved_at: dict[str, float] = {}
RISK_EVENT_COOLDOWN_SECONDS = 15.0


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    seed_demo_user_if_empty()


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> UserPublic:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired. Please sign in again.",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session",
        )
    email = payload.get("sub")
    display_name = payload.get("name")
    if not email or not isinstance(email, str):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session",
        )
    return UserPublic(email=email, display_name=str(display_name or ""))


@app.get("/")
def root() -> dict[str, str]:
    """Landing JSON when someone opens the API base URL in a browser."""
    return {
        "service": app.title,
        "version": app.version,
        "docs": "/docs",
        "openapi": "/openapi.json",
        "health": "/health",
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest) -> TokenResponse:
    user = get_user_by_email(payload.email)
    if user is None or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )
    token = create_access_token(
        subject_email=user["email"],
        display_name=user["display_name"],
    )
    return TokenResponse(access_token=token)


@app.post("/api/auth/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest) -> TokenResponse:
    display_name = payload.display_name.strip()
    if not display_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Display name is required.",
        )
    try:
        user = create_user(
            email=payload.email,
            password_hash=hash_password(payload.password),
            display_name=display_name,
        )
    except UserAlreadyExistsError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    token = create_access_token(
        subject_email=user["email"],
        display_name=user["display_name"],
    )
    return TokenResponse(access_token=token)


@app.get("/api/auth/me", response_model=UserPublic)
def me(current: UserPublic = Depends(get_current_user)) -> UserPublic:
    return current


@app.post("/api/admin/reset-demo-users")
def admin_reset_demo_users(
    _: UserPublic = Depends(get_current_user),
    admin_reset_token: str | None = Header(default=None, alias="X-ErgoPilot-Admin-Reset"),
) -> dict[str, object]:
    if admin_reset_token != "demo-only":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing or invalid admin reset token.",
        )
    result = reset_users_to_demo()
    return {
        "status": "ok",
        "deleted_user_count": result["deleted_user_count"],
        "demo_email": result["demo_email"],
    }


@app.post("/api/calibrate")
def calibrate(
    payload: CalibrationRequest,
    _: UserPublic = Depends(get_current_user),
) -> dict[str, object]:
    profile = build_calibration_profile(payload.landmarks)
    calibration_profiles[payload.worker_id] = profile
    return {
        "worker_id": payload.worker_id,
        "calibration": profile.model_dump(),
        "message": "Calibration saved for worker.",
    }


@app.post("/api/analyze")
def analyze(
    payload: AnalyzeRequest,
    _: UserPublic = Depends(get_current_user),
) -> dict[str, object]:
    profile = calibration_profiles.get(payload.worker_id)
    result = analyze_pose(
        worker_id=payload.worker_id,
        landmarks=payload.landmarks,
        load_kg=payload.load_kg,
        frequency_lifts_per_min=payload.frequency_lifts_per_min,
        calibration=profile,
    )
    posture_sample_id = insert_posture_sample(
        worker_id=result.worker_id,
        risk_level=result.risk_level,
        rula_score=result.rula_score,
        reba_score=result.reba_score,
        rwl_kg=result.rwl_kg,
        niosh_ratio=result.niosh_ratio,
        frame_ts_ms=payload.frame_ts,
    )

    risk_event_id: int | None = None
    if result.risk_level in {"warning", "danger"}:
        now = time.time()
        last_saved = risk_event_last_saved_at.get(result.worker_id, 0.0)
        if now - last_saved >= RISK_EVENT_COOLDOWN_SECONDS:
            # Persist only skeletal points and derived risk metadata.
            risk_event_id = insert_risk_event(
                worker_id=result.worker_id,
                risk_level=result.risk_level,
                rula_score=result.rula_score,
                reba_score=result.reba_score,
                rwl_kg=result.rwl_kg,
                niosh_ratio=result.niosh_ratio,
                landmarks_payload=[lm.model_dump() for lm in payload.landmarks],
            )
            risk_event_last_saved_at[result.worker_id] = now
    return result.model_copy(
        update={"posture_sample_id": posture_sample_id, "risk_event_id": risk_event_id}
    ).model_dump()


@app.get("/api/events")
def events(
    limit: int = Query(default=50, ge=1, le=500),
    _: UserPublic = Depends(get_current_user),
) -> dict[str, object]:
    return {"count": limit, "items": get_recent_events(limit)}


@app.post("/api/improvement-summary", response_model=ImprovementSummaryResponse)
def improvement_summary(
    payload: ImprovementSummaryRequest,
    _: UserPublic = Depends(get_current_user),
) -> ImprovementSummaryResponse:
    summary, source, model = synthesize_improvement_summary(
        [clip.model_dump() for clip in payload.clips]
    )
    return ImprovementSummaryResponse(summary=summary, source=source, model=model)


@app.delete("/api/events/{event_id}")
def delete_event(
    event_id: int,
    _: UserPublic = Depends(get_current_user),
) -> dict[str, object]:
    deleted = delete_risk_event(event_id)
    return {"event_id": event_id, "deleted": deleted}


@app.delete("/api/events")
def clear_events(_: UserPublic = Depends(get_current_user)) -> dict[str, object]:
    deleted_event_count = clear_risk_events()
    deleted_sample_count = clear_posture_samples()
    return {
        "deleted_risk_event_count": deleted_event_count,
        "deleted_posture_sample_count": deleted_sample_count,
    }


@app.delete("/api/session-samples/{sample_id}")
def delete_session_sample(
    sample_id: int,
    _: UserPublic = Depends(get_current_user),
) -> dict[str, object]:
    deleted = delete_posture_sample(sample_id)
    return {"sample_id": sample_id, "deleted": deleted}


@app.delete("/api/session-samples-window")
def delete_session_samples_window(
    worker_id: str = Query(min_length=1, max_length=128),
    start_ms: float = Query(ge=0.0),
    end_ms: float = Query(ge=0.0),
    _: UserPublic = Depends(get_current_user),
) -> dict[str, object]:
    if end_ms < start_ms:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="end_ms must be greater than or equal to start_ms",
        )
    deleted_count = delete_posture_samples_in_window(worker_id, start_ms, end_ms)
    return {"worker_id": worker_id, "deleted_count": deleted_count}


@app.get("/api/session-averages")
def session_averages(
    days: int = Query(default=7, ge=1, le=365),
    worker_id: str | None = Query(default=None, min_length=1, max_length=128),
    _: UserPublic = Depends(get_current_user),
) -> dict[str, object]:
    averages = get_session_averages(days, worker_id=worker_id)
    return {
        "days": days,
        "worker_id": worker_id,
        "sample_count": averages["sample_count"],
        "rula_avg": averages["rula_avg"],
        "reba_avg": averages["reba_avg"],
    }
