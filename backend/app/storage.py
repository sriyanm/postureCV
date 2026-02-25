import json
import sqlite3
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent.parent / "ergopilot.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS risk_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                worker_id TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                rula_score INTEGER NOT NULL,
                reba_score INTEGER NOT NULL,
                rwl_kg REAL NOT NULL,
                niosh_ratio REAL NOT NULL,
                landmarks_json TEXT NOT NULL
            );
            """
        )


def insert_risk_event(
    worker_id: str,
    risk_level: str,
    rula_score: int,
    reba_score: int,
    rwl_kg: float,
    niosh_ratio: float,
    landmarks_payload: list[dict[str, Any]],
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO risk_events (
                worker_id, risk_level, rula_score, reba_score, rwl_kg, niosh_ratio, landmarks_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            (
                worker_id,
                risk_level,
                rula_score,
                reba_score,
                rwl_kg,
                niosh_ratio,
                json.dumps(landmarks_payload),
            ),
        )


def get_recent_events(limit: int = 50) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                id, created_at, worker_id, risk_level, rula_score, reba_score, rwl_kg, niosh_ratio, landmarks_json
            FROM risk_events
            ORDER BY id DESC
            LIMIT ?;
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]
