#!/usr/bin/env python3
"""
Content Pipeline — SQLite database schema and helpers.
DB path: <repo>/data/pipeline.db
"""

import sqlite3, os, datetime as dt
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "pipeline.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lines (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'draft',
    video_ref_path  TEXT,
    photo_ref_path  TEXT,
    face_ref_name   TEXT DEFAULT 'reference_face.jpg',
    positive_prompt TEXT DEFAULT '',
    negative_prompt TEXT DEFAULT '',
    drive_folder_id TEXT DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_steps (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    line_id         INTEGER NOT NULL REFERENCES lines(id) ON DELETE CASCADE,
    step_type       TEXT NOT NULL,
    input_path      TEXT,
    output_path     TEXT,
    prompt_text     TEXT,
    seed            INTEGER,
    comfy_prompt_id TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    accepted        INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL,
    UNIQUE(line_id, step_type)
);

CREATE TABLE IF NOT EXISTS photos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    line_id         INTEGER NOT NULL REFERENCES lines(id) ON DELETE CASCADE,
    filename        TEXT NOT NULL,
    prompt_text     TEXT,
    description     TEXT,
    seed            INTEGER,
    comfy_prompt_id TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    qa_status       TEXT DEFAULT 'pending',
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS videos (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    line_id          INTEGER NOT NULL REFERENCES lines(id) ON DELETE CASCADE,
    photo_id         INTEGER NOT NULL REFERENCES photos(id),
    filename         TEXT,
    comfy_prompt_id  TEXT,
    vast_instance_id INTEGER,
    status           TEXT NOT NULL DEFAULT 'queued',
    qa_status        TEXT DEFAULT 'pending',
    progress_text    TEXT DEFAULT '',
    cost_usd         REAL DEFAULT 0,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drive_uploads (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    line_id     INTEGER NOT NULL REFERENCES lines(id) ON DELETE CASCADE,
    item_type   TEXT NOT NULL,
    item_id     INTEGER NOT NULL,
    drive_file_id TEXT,
    drive_link  TEXT,
    status      TEXT NOT NULL DEFAULT 'pending',
    error       TEXT,
    created_at  TEXT NOT NULL
);
"""


def _now():
    return dt.datetime.now(dt.UTC).isoformat()


def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def init_db():
    db = get_db()
    db.executescript(SCHEMA)
    db.commit()
    db.close()


# --------------- Settings ---------------

def get_setting(key: str, default: str = "") -> str:
    db = get_db()
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    db.close()
    return row["value"] if row else default


def get_all_settings() -> dict:
    db = get_db()
    rows = db.execute("SELECT key, value FROM settings").fetchall()
    db.close()
    return {r["key"]: r["value"] for r in rows}


def set_setting(key: str, value: str):
    db = get_db()
    db.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, value, _now())
    )
    db.commit()
    db.close()


# --------------- Lines ---------------

def create_line(name: str, **kwargs) -> int:
    db = get_db()
    now = _now()
    cols = ["name", "created_at", "updated_at"]
    vals = [name, now, now]
    for k in ("video_ref_path", "photo_ref_path", "face_ref_name",
              "positive_prompt", "negative_prompt", "drive_folder_id", "status"):
        if k in kwargs:
            cols.append(k)
            vals.append(kwargs[k])
    placeholders = ",".join(["?"] * len(cols))
    col_str = ",".join(cols)
    cur = db.execute(f"INSERT INTO lines ({col_str}) VALUES ({placeholders})", vals)
    db.commit()
    lid = cur.lastrowid
    db.close()
    return lid


def get_line(line_id: int) -> dict | None:
    db = get_db()
    row = db.execute("SELECT * FROM lines WHERE id=?", (line_id,)).fetchone()
    if not row:
        db.close()
        return None
    line = dict(row)

    # pipeline steps
    steps = db.execute("SELECT * FROM pipeline_steps WHERE line_id=? ORDER BY id", (line_id,)).fetchall()
    line["steps"] = {s["step_type"]: dict(s) for s in steps}

    # photos
    photos = db.execute("SELECT * FROM photos WHERE line_id=? ORDER BY id", (line_id,)).fetchall()
    line["photos"] = [dict(p) for p in photos]

    # videos
    videos = db.execute("SELECT * FROM videos WHERE line_id=? ORDER BY id", (line_id,)).fetchall()
    line["videos"] = [dict(v) for v in videos]

    # drive uploads
    uploads = db.execute("SELECT * FROM drive_uploads WHERE line_id=? ORDER BY id", (line_id,)).fetchall()
    line["drive_uploads"] = [dict(u) for u in uploads]

    db.close()
    return line


def list_lines() -> list[dict]:
    db = get_db()
    rows = db.execute("SELECT * FROM lines ORDER BY id DESC").fetchall()
    result = []
    for row in rows:
        line = dict(row)
        line["photo_count"] = db.execute(
            "SELECT COUNT(*) as c FROM photos WHERE line_id=?", (row["id"],)
        ).fetchone()["c"]
        line["photo_accepted"] = db.execute(
            "SELECT COUNT(*) as c FROM photos WHERE line_id=? AND qa_status='accepted'", (row["id"],)
        ).fetchone()["c"]
        line["video_count"] = db.execute(
            "SELECT COUNT(*) as c FROM videos WHERE line_id=?", (row["id"],)
        ).fetchone()["c"]
        line["video_done"] = db.execute(
            "SELECT COUNT(*) as c FROM videos WHERE line_id=? AND status='done'", (row["id"],)
        ).fetchone()["c"]
        result.append(line)
    db.close()
    return result


def update_line(line_id: int, **kwargs):
    if not kwargs:
        return
    db = get_db()
    kwargs["updated_at"] = _now()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [line_id]
    db.execute(f"UPDATE lines SET {sets} WHERE id=?", vals)
    db.commit()
    db.close()


def delete_line(line_id: int):
    db = get_db()
    db.execute("DELETE FROM lines WHERE id=?", (line_id,))
    db.commit()
    db.close()


# --------------- Pipeline Steps ---------------

def upsert_step(line_id: int, step_type: str, **kwargs) -> int:
    db = get_db()
    now = _now()
    existing = db.execute(
        "SELECT id FROM pipeline_steps WHERE line_id=? AND step_type=?",
        (line_id, step_type)
    ).fetchone()

    if existing:
        sid = existing["id"]
        if kwargs:
            sets = ", ".join(f"{k}=?" for k in kwargs)
            vals = list(kwargs.values()) + [sid]
            db.execute(f"UPDATE pipeline_steps SET {sets} WHERE id=?", vals)
    else:
        cols = ["line_id", "step_type", "created_at"]
        vals = [line_id, step_type, now]
        for k, v in kwargs.items():
            cols.append(k)
            vals.append(v)
        placeholders = ",".join(["?"] * len(cols))
        col_str = ",".join(cols)
        cur = db.execute(f"INSERT INTO pipeline_steps ({col_str}) VALUES ({placeholders})", vals)
        sid = cur.lastrowid

    db.commit()
    db.close()
    return sid


def get_step(line_id: int, step_type: str) -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT * FROM pipeline_steps WHERE line_id=? AND step_type=?",
        (line_id, step_type)
    ).fetchone()
    db.close()
    return dict(row) if row else None


# --------------- Photos ---------------

def add_photo(line_id: int, filename: str, **kwargs) -> int:
    db = get_db()
    now = _now()
    cols = ["line_id", "filename", "created_at"]
    vals = [line_id, filename, now]
    for k in ("prompt_text", "description", "seed", "comfy_prompt_id", "status", "qa_status"):
        if k in kwargs:
            cols.append(k)
            vals.append(kwargs[k])
    placeholders = ",".join(["?"] * len(cols))
    col_str = ",".join(cols)
    cur = db.execute(f"INSERT INTO photos ({col_str}) VALUES ({placeholders})", vals)
    db.commit()
    pid = cur.lastrowid
    db.close()
    return pid


def update_photo(photo_id: int, **kwargs):
    if not kwargs:
        return
    db = get_db()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [photo_id]
    db.execute(f"UPDATE photos SET {sets} WHERE id=?", vals)
    db.commit()
    db.close()


def get_photo(photo_id: int) -> dict | None:
    db = get_db()
    row = db.execute("SELECT * FROM photos WHERE id=?", (photo_id,)).fetchone()
    db.close()
    return dict(row) if row else None


def get_photos_by_line(line_id: int, qa_status: str | None = None) -> list[dict]:
    db = get_db()
    if qa_status:
        rows = db.execute(
            "SELECT * FROM photos WHERE line_id=? AND qa_status=? ORDER BY id",
            (line_id, qa_status)
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM photos WHERE line_id=? ORDER BY id", (line_id,)).fetchall()
    db.close()
    return [dict(r) for r in rows]


# --------------- Videos ---------------

def add_video(line_id: int, photo_id: int, **kwargs) -> int:
    db = get_db()
    now = _now()
    cols = ["line_id", "photo_id", "created_at"]
    vals = [line_id, photo_id, now]
    for k in ("filename", "comfy_prompt_id", "vast_instance_id", "status", "qa_status", "progress_text", "cost_usd"):
        if k in kwargs:
            cols.append(k)
            vals.append(kwargs[k])
    placeholders = ",".join(["?"] * len(cols))
    col_str = ",".join(cols)
    cur = db.execute(f"INSERT INTO videos ({col_str}) VALUES ({placeholders})", vals)
    db.commit()
    vid = cur.lastrowid
    db.close()
    return vid


def update_video(video_id: int, **kwargs):
    if not kwargs:
        return
    db = get_db()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [video_id]
    db.execute(f"UPDATE videos SET {sets} WHERE id=?", vals)
    db.commit()
    db.close()


def get_video(video_id: int) -> dict | None:
    db = get_db()
    row = db.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
    db.close()
    return dict(row) if row else None


def get_videos_by_line(line_id: int) -> list[dict]:
    db = get_db()
    rows = db.execute("SELECT * FROM videos WHERE line_id=? ORDER BY id", (line_id,)).fetchall()
    db.close()
    return [dict(r) for r in rows]


# --------------- Drive Uploads ---------------

def add_drive_upload(line_id: int, item_type: str, item_id: int) -> int:
    db = get_db()
    now = _now()
    cur = db.execute(
        "INSERT INTO drive_uploads (line_id, item_type, item_id, status, created_at) VALUES (?,?,?,?,?)",
        (line_id, item_type, item_id, "pending", now)
    )
    db.commit()
    uid = cur.lastrowid
    db.close()
    return uid


def update_drive_upload(upload_id: int, **kwargs):
    if not kwargs:
        return
    db = get_db()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [upload_id]
    db.execute(f"UPDATE drive_uploads SET {sets} WHERE id=?", vals)
    db.commit()
    db.close()


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
