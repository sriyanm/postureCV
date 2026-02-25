import math

from app.schemas import AnalyzeResult, CalibrationProfile, Landmark


# MediaPipe pose landmark IDs used in this prototype.
NOSE = 0
LEFT_SHOULDER = 11
RIGHT_SHOULDER = 12
LEFT_ELBOW = 13
RIGHT_ELBOW = 14
LEFT_WRIST = 15
RIGHT_WRIST = 16
LEFT_HIP = 23
RIGHT_HIP = 24
LEFT_KNEE = 25
RIGHT_KNEE = 26
LEFT_ANKLE = 27
RIGHT_ANKLE = 28


def _to_map(landmarks: list[Landmark]) -> dict[int, Landmark]:
    return {lm.id: lm for lm in landmarks}


def _midpoint(a: Landmark, b: Landmark) -> tuple[float, float]:
    return ((a.x + b.x) / 2.0, (a.y + b.y) / 2.0)


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _angle_abc(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    ba = (a[0] - b[0], a[1] - b[1])
    bc = (c[0] - b[0], c[1] - b[1])
    dot = ba[0] * bc[0] + ba[1] * bc[1]
    ba_mag = math.hypot(*ba)
    bc_mag = math.hypot(*bc)
    if ba_mag == 0 or bc_mag == 0:
        return 0.0
    value = max(-1.0, min(1.0, dot / (ba_mag * bc_mag)))
    return math.degrees(math.acos(value))


def _tilt_from_vertical_deg(top: tuple[float, float], bottom: tuple[float, float]) -> float:
    vec = (top[0] - bottom[0], top[1] - bottom[1])
    # In image coordinates, "up" is negative y.
    angle = math.degrees(math.atan2(vec[0], -vec[1]))
    return abs(angle)


def build_calibration_profile(landmarks: list[Landmark]) -> CalibrationProfile:
    lm = _to_map(landmarks)
    shoulder_mid = _midpoint(lm[LEFT_SHOULDER], lm[RIGHT_SHOULDER])
    hip_mid = _midpoint(lm[LEFT_HIP], lm[RIGHT_HIP])
    shoulder_width = _distance(
        (lm[LEFT_SHOULDER].x, lm[LEFT_SHOULDER].y),
        (lm[RIGHT_SHOULDER].x, lm[RIGHT_SHOULDER].y),
    )
    hip_width = _distance(
        (lm[LEFT_HIP].x, lm[LEFT_HIP].y),
        (lm[RIGHT_HIP].x, lm[RIGHT_HIP].y),
    )
    trunk_tilt = _tilt_from_vertical_deg(shoulder_mid, hip_mid)
    return CalibrationProfile(
        shoulder_width=shoulder_width,
        hip_width=hip_width,
        trunk_tilt_baseline_deg=trunk_tilt,
    )


def analyze_pose(
    worker_id: str,
    landmarks: list[Landmark],
    load_kg: float,
    frequency_lifts_per_min: float,
    calibration: CalibrationProfile | None = None,
) -> AnalyzeResult:
    lm = _to_map(landmarks)

    l_shoulder = (lm[LEFT_SHOULDER].x, lm[LEFT_SHOULDER].y)
    r_shoulder = (lm[RIGHT_SHOULDER].x, lm[RIGHT_SHOULDER].y)
    l_elbow = (lm[LEFT_ELBOW].x, lm[LEFT_ELBOW].y)
    r_elbow = (lm[RIGHT_ELBOW].x, lm[RIGHT_ELBOW].y)
    l_wrist = (lm[LEFT_WRIST].x, lm[LEFT_WRIST].y)
    r_wrist = (lm[RIGHT_WRIST].x, lm[RIGHT_WRIST].y)
    l_hip = (lm[LEFT_HIP].x, lm[LEFT_HIP].y)
    r_hip = (lm[RIGHT_HIP].x, lm[RIGHT_HIP].y)
    l_knee = (lm[LEFT_KNEE].x, lm[LEFT_KNEE].y)
    r_knee = (lm[RIGHT_KNEE].x, lm[RIGHT_KNEE].y)
    l_ankle = (lm[LEFT_ANKLE].x, lm[LEFT_ANKLE].y)
    r_ankle = (lm[RIGHT_ANKLE].x, lm[RIGHT_ANKLE].y)
    nose = (lm[NOSE].x, lm[NOSE].y)

    shoulder_mid = _midpoint(lm[LEFT_SHOULDER], lm[RIGHT_SHOULDER])
    hip_mid = _midpoint(lm[LEFT_HIP], lm[RIGHT_HIP])
    ankle_mid = _midpoint(lm[LEFT_ANKLE], lm[RIGHT_ANKLE])
    wrist_mid = ((l_wrist[0] + r_wrist[0]) / 2.0, (l_wrist[1] + r_wrist[1]) / 2.0)

    trunk_tilt = _tilt_from_vertical_deg(shoulder_mid, hip_mid)
    neck_tilt = _tilt_from_vertical_deg(nose, shoulder_mid)
    left_elbow_angle = _angle_abc(l_shoulder, l_elbow, l_wrist)
    right_elbow_angle = _angle_abc(r_shoulder, r_elbow, r_wrist)
    left_knee_angle = _angle_abc(l_hip, l_knee, l_ankle)
    right_knee_angle = _angle_abc(r_hip, r_knee, r_ankle)

    if calibration:
        trunk_tilt = max(0.0, trunk_tilt - calibration.trunk_tilt_baseline_deg)

    # ---- RULA proxy scoring (1-7) ----
    rula = 1
    notes: list[str] = []
    elbow_extension = 180.0 - min(left_elbow_angle, right_elbow_angle)
    if elbow_extension > 20:
        rula += 1
        notes.append("Elbow flexion outside neutral range.")
    if elbow_extension > 45:
        rula += 1
    if trunk_tilt > 10:
        rula += 1
        notes.append("Trunk is leaning.")
    if trunk_tilt > 25:
        rula += 1
    if neck_tilt > 15:
        rula += 1
        notes.append("Neck tilt elevated.")
    rula = min(7, rula)

    # ---- REBA proxy scoring (1-15) ----
    reba = 1
    knee_bend = 180.0 - min(left_knee_angle, right_knee_angle)
    if trunk_tilt > 15:
        reba += 2
    if trunk_tilt > 30:
        reba += 2
    if neck_tilt > 20:
        reba += 1
    if knee_bend > 20:
        reba += 2
        notes.append("Leg support appears unstable.")
    if knee_bend > 40:
        reba += 2
    if elbow_extension > 35:
        reba += 1
    reba = min(15, reba)

    # ---- NIOSH proxy: RWL and lifting index ----
    # This is a simplified approximation from image-space distances.
    horizontal_dist = max(0.1, abs(wrist_mid[0] - ankle_mid[0]))
    vertical_dist = max(0.1, abs(wrist_mid[1] - hip_mid[1]))
    travel_dist = max(0.1, abs(wrist_mid[1] - ankle_mid[1]))
    asymmetry_deg = min(90.0, abs(l_shoulder[0] - r_shoulder[0]) * 100.0)

    hm = min(1.0, 0.25 / horizontal_dist)
    vm = max(0.0, 1.0 - 0.003 * abs(vertical_dist * 100.0 - 75.0))
    dm = max(0.0, min(1.0, 0.82 + (4.5 / (travel_dist * 100.0))))
    am = max(0.0, 1.0 - 0.0032 * asymmetry_deg)
    fm = max(0.2, 1.0 - 0.03 * frequency_lifts_per_min)
    cm = 0.9  # Assume fair coupling for prototype.
    lc = 23.0

    rwl = lc * hm * vm * dm * am * fm * cm
    rwl = max(0.0, min(23.0, rwl))
    niosh_ratio = (load_kg / rwl) if rwl > 0 else float("inf")
    if niosh_ratio > 1.0:
        notes.append("Load exceeds recommended limit (NIOSH proxy).")

    if rula >= 6 or reba >= 10 or niosh_ratio > 1.2:
        risk_level = "danger"
    elif rula >= 4 or reba >= 6 or niosh_ratio > 1.0:
        risk_level = "warning"
    else:
        risk_level = "safe"

    return AnalyzeResult(
        worker_id=worker_id,
        risk_level=risk_level,
        rula_score=rula,
        reba_score=reba,
        rwl_kg=round(rwl, 2),
        niosh_ratio=round(niosh_ratio, 2) if math.isfinite(niosh_ratio) else 99.0,
        notes=notes,
        derived_angles_deg={
            "trunk_tilt": round(trunk_tilt, 2),
            "neck_tilt": round(neck_tilt, 2),
            "left_elbow": round(left_elbow_angle, 2),
            "right_elbow": round(right_elbow_angle, 2),
            "left_knee": round(left_knee_angle, 2),
            "right_knee": round(right_knee_angle, 2),
        },
    )
