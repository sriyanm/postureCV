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
    notes: list[str]
    derived_angles_deg: dict[str, float]
