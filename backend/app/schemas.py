from typing import Literal

from pydantic import BaseModel, Field


RiskLevel = Literal["safe", "warning", "danger"]


class Landmark(BaseModel):
    id: int = Field(ge=0)
    x: float
    y: float
    z: float = 0.0
    visibility: float | None = Field(default=None, ge=0.0, le=1.0)


class CalibrationRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=128)
    landmarks: list[Landmark] = Field(min_length=17)


class AnalyzeRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=128)
    landmarks: list[Landmark] = Field(min_length=17)
    frame_ts: float | None = None
    load_kg: float = Field(default=10.0, ge=0.0, le=200.0)
    frequency_lifts_per_min: float = Field(default=2.0, ge=0.0, le=20.0)


class CalibrationProfile(BaseModel):
    shoulder_width: float
    hip_width: float
    trunk_tilt_baseline_deg: float


class AnalyzeResult(BaseModel):
    worker_id: str
    risk_level: RiskLevel
    rula_score: int
    reba_score: int
    rwl_kg: float
    niosh_ratio: float
    posture_sample_id: int | None = None
    risk_event_id: int | None = None
    notes: list[str]
    derived_angles_deg: dict[str, float]


class LoginRequest(BaseModel):
    email: str = Field(
        min_length=3,
        max_length=255,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    password: str = Field(min_length=8, max_length=256)


class SignupRequest(BaseModel):
    email: str = Field(
        min_length=3,
        max_length=255,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    password: str = Field(min_length=8, max_length=256)
    display_name: str = Field(min_length=1, max_length=120)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserPublic(BaseModel):
    email: str
    display_name: str


class ImprovementSummaryClip(BaseModel):
    risk_level: RiskLevel | None = None
    risk_descriptions: list[str] = Field(default_factory=list, max_length=8)
    primary_risk_description: str | None = Field(default=None, max_length=400)
    rula_score: int | None = Field(default=None, ge=1, le=7)
    reba_score: int | None = Field(default=None, ge=1, le=15)
    niosh_ratio: float | None = Field(default=None, ge=0.0, le=99.0)


class ImprovementSummaryRequest(BaseModel):
    clips: list[ImprovementSummaryClip] = Field(default_factory=list, max_length=200)


class ImprovementSummaryResponse(BaseModel):
    summary: str
    source: Literal["llm", "fallback"]
    model: str | None = None
