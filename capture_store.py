from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

DB_PATH = Path(os.getenv("CAPTURE_DB_PATH", str(Path(__file__).with_name("capture.sqlite"))))
_LOCK = threading.Lock()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS capture_sessions (
          id TEXT PRIMARY KEY,
          started_at REAL NOT NULL,
          ended_at REAL
        );
        CREATE TABLE IF NOT EXISTS capture_records (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id TEXT NOT NULL,
          created_at REAL NOT NULL,
          method TEXT NOT NULL,
          path TEXT NOT NULL,
          request_body TEXT,
          response_body TEXT,
          prompt TEXT,
          model TEXT,
          status_code INTEGER,
          duration_ms INTEGER,
          request_id TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_capture_records_session ON capture_records(session_id, id DESC);
        """)


def start_session() -> dict[str, Any]:
    init_db()
    session_id = uuid.uuid4().hex
    with _LOCK, sqlite3.connect(DB_PATH) as db:
        db.execute("UPDATE capture_sessions SET ended_at=? WHERE ended_at IS NULL", (time.time(),))
        db.execute("INSERT INTO capture_sessions(id, started_at) VALUES (?, ?)", (session_id, time.time()))
        db.commit()
    return {"id": session_id, "started_at": time.time(), "enabled": True}


def stop_session() -> dict[str, Any]:
    init_db()
    with _LOCK, sqlite3.connect(DB_PATH) as db:
        row = db.execute("SELECT id, started_at FROM capture_sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1").fetchone()
        if not row:
            return {"enabled": False}
        ended = time.time()
        db.execute("UPDATE capture_sessions SET ended_at=? WHERE id=?", (ended, row[0]))
        db.commit()
        return {"enabled": False, "id": row[0], "started_at": row[1], "ended_at": ended}


def status() -> dict[str, Any]:
    init_db()
    with sqlite3.connect(DB_PATH) as db:
        row = db.execute("SELECT id, started_at FROM capture_sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1").fetchone()
    return {"enabled": bool(row), "id": row[0] if row else None, "started_at": row[1] if row else None}


def _extract_prompt(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("prompt", "input", "instructions", "messages", "contents"):
            if key in value:
                return value[key]
        if "content" in value and isinstance(value["content"], (str, list, dict)):
            return value["content"]
        for item in value.values():
            found = _extract_prompt(item)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _extract_prompt(item)
            if found is not None:
                return found
    return None


def prompt_from_body(body: bytes) -> str:
    if not body:
        return ""
    try:
        value = json.loads(body.decode("utf-8", "replace"))
        found = _extract_prompt(value)
        return json.dumps(found, ensure_ascii=False) if not isinstance(found, str) else found
    except Exception:
        return ""


def _clip(body: bytes | str | None) -> str:
    if body is None:
        return ""
    if isinstance(body, bytes):
        return body[:MAX_BODY].decode("utf-8", "replace")
    return body[:MAX_BODY]


def add_record(session_id: str, method: str, path: str, request_body: bytes, response_body: bytes, status_code: int, duration_ms: int, request_id: str = "") -> None:
    init_db()
    request_text = _clip(request_body)
    response_text = _clip(response_body)
    prompt = prompt_from_body(request_body)
    model = ""
    try:
        data = json.loads(request_text)
        model = str(data.get("model", "")) if isinstance(data, dict) else ""
    except Exception:
        pass
    with _LOCK, sqlite3.connect(DB_PATH) as db:
        db.execute(
            """INSERT INTO capture_records(session_id,created_at,method,path,request_body,response_body,prompt,model,status_code,duration_ms,request_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (session_id, time.time(), method, path, request_text, response_text, prompt, model, status_code, duration_ms, request_id),
        )
        db.commit()


def list_records(limit: int = 100) -> list[dict[str, Any]]:
    init_db()
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT id,session_id,created_at,method,path,prompt,model,status_code,duration_ms,request_id FROM capture_records ORDER BY id DESC LIMIT ?",
            (min(max(limit, 1), 500),),
        ).fetchall()
    return [dict(row) for row in rows]


def get_record(record_id: int) -> dict[str, Any] | None:
    init_db()
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM capture_records WHERE id=?", (record_id,)).fetchone()
    return dict(row) if row else None


