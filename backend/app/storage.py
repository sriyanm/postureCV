import json
import sqlite3
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent.parent / "ergopilot.db"


class UserAlreadyExistsError(ValueError):
    pass


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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                display_name TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS posture_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                worker_id TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                rula_score INTEGER NOT NULL,
                reba_score INTEGER NOT NULL,
                rwl_kg REAL NOT NULL,
                niosh_ratio REAL NOT NULL,
                frame_ts_ms REAL
            );
            """
        )
        cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(posture_samples);").fetchall()
        }
        if "frame_ts_ms" not in cols:
            conn.execute("ALTER TABLE posture_samples ADD COLUMN frame_ts_ms REAL;")


def seed_demo_user_if_empty() -> None:
    """Insert a local demo account when the users table has no rows."""
    from app.auth import hash_password

    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
        if row is None or int(row["c"]) > 0:
            return
        demo_email = "demo@ergopilot.local"
        demo_password = "changeme"
        display_name = "Demo Worker"
        conn.execute(
            """
            INSERT INTO users (email, password_hash, display_name)
            VALUES (?, ?, ?);
            """,
            (demo_email.lower(), hash_password(demo_password), display_name),
        )


def get_user_by_email(email: str) -> dict[str, Any] | None:
    normalized = email.strip().lower()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, email, password_hash, display_name FROM users WHERE email = ? LIMIT 1;",
            (normalized,),
        ).fetchone()
    return dict(row) if row else None


def create_user(email: str, password_hash: str, display_name: str) -> dict[str, Any]:
    normalized_email = email.strip().lower()
    normalized_name = display_name.strip()
    try:
        with get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO users (email, password_hash, display_name)
                VALUES (?, ?, ?);
                """,
                (normalized_email, password_hash, normalized_name),
            )
            row_id = cursor.lastrowid
            if row_id is None:
                raise RuntimeError("Failed to create user.")
            row = conn.execute(
                "SELECT id, email, display_name FROM users WHERE id = ? LIMIT 1;",
                (row_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Created user could not be loaded.")
            return dict(row)
    except sqlite3.IntegrityError as exc:
        raise UserAlreadyExistsError("An account with this email already exists.") from exc


def reset_users_to_demo() -> dict[str, Any]:
    from app.auth import hash_password

    demo_email = "demo@ergopilot.local"
    demo_password = "changeme"
    demo_display_name = "Demo Worker"
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM users WHERE lower(email) != ?;",
            (demo_email,),
        )
        deleted_user_count = int(cursor.rowcount)
        conn.execute(
            """
            INSERT INTO users (email, password_hash, display_name)
            VALUES (?, ?, ?)
            ON CONFLICT(email)
            DO UPDATE SET
                password_hash = excluded.password_hash,
                display_name = excluded.display_name;
            """,
            (demo_email, hash_password(demo_password), demo_display_name),
        )
    return {
        "deleted_user_count": deleted_user_count,
        "demo_email": demo_email,
    }


def insert_risk_event(
    worker_id: str,
    risk_level: str,
    rula_score: int,
    reba_score: int,
    rwl_kg: float,
    niosh_ratio: float,
    landmarks_payload: list[dict[str, Any]],
) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
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
        row_id = cursor.lastrowid
        if row_id is None:
            raise RuntimeError("Failed to insert risk event.")
        return row_id


def insert_posture_sample(
    worker_id: str,
    risk_level: str,
    rula_score: int,
    reba_score: int,
    rwl_kg: float,
    niosh_ratio: float,
    frame_ts_ms: float | None,
) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO posture_samples (
                worker_id, risk_level, rula_score, reba_score, rwl_kg, niosh_ratio, frame_ts_ms
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
                frame_ts_ms,
            ),
        )
        row_id = cursor.lastrowid
        if row_id is None:
            raise RuntimeError("Failed to insert posture sample.")
        return row_id


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


def delete_risk_event(event_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM risk_events WHERE id = ?;", (event_id,))
        return int(cursor.rowcount) > 0


def clear_risk_events() -> int:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM risk_events;")
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'risk_events';")
        return int(cursor.rowcount)


def clear_posture_samples() -> int:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM posture_samples;")
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'posture_samples';")
        return int(cursor.rowcount)


def delete_posture_sample(sample_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM posture_samples WHERE id = ?;", (sample_id,))
        return int(cursor.rowcount) > 0


def delete_posture_samples_in_window(worker_id: str, start_ms: float, end_ms: float) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            DELETE FROM posture_samples
            WHERE worker_id = ?
              AND frame_ts_ms IS NOT NULL
              AND frame_ts_ms >= ?
              AND frame_ts_ms <= ?;
            """,
            (worker_id, start_ms, end_ms),
        )
        return int(cursor.rowcount)


def get_session_averages(days: int, worker_id: str | None = None) -> dict[str, Any]:
    interval = f"-{days} days"
    normalized_worker_id = (worker_id or "").strip()
    with get_connection() as conn:
        if normalized_worker_id:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS sample_count,
                    AVG(rula_score) AS rula_avg,
                    AVG(reba_score) AS reba_avg
                FROM posture_samples
                WHERE created_at >= datetime('now', ?)
                  AND worker_id = ?;
                """,
                (interval, normalized_worker_id),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS sample_count,
                    AVG(rula_score) AS rula_avg,
                    AVG(reba_score) AS reba_avg
                FROM posture_samples
                WHERE created_at >= datetime('now', ?);
                """,
                (interval,),
            ).fetchone()
    if row is None:
        return {"sample_count": 0, "rula_avg": None, "reba_avg": None}
    return {
        "sample_count": int(row["sample_count"] or 0),
        "rula_avg": float(row["rula_avg"]) if row["rula_avg"] is not None else None,
        "reba_avg": float(row["reba_avg"]) if row["reba_avg"] is not None else None,
    }
