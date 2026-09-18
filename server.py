"""NewAPI Logs Reader - small read-only FastAPI console.

The service reads the New API ``logs`` table without modifying it.  Set
``NEWAPI_DB_URL`` to a SQLite path (``sqlite:///...`` or a plain path) or to a
PostgreSQL URL.  The API keeps the database adapter deliberately small so it
can run against New API installations that have a different set of optional
columns.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles


PORT = int(os.getenv("PORT", os.getenv("APP_PORT", "3333")))
HOST = os.getenv("HOST", os.getenv("APP_HOST", "0.0.0.0"))
DB_URL = os.getenv("NEWAPI_DB_URL", os.getenv("LOG_DB_DSN", os.getenv("DB_DSN", "")))
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def _json(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list, int, float, bool)):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return str(value)


def _text(value: Any) -> str:
    value = _json(value)
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _walk(value: Any, keys: set[str]) -> list[Any]:
    """Collect values for keys in a nested New API ``other`` payload."""
    found: list[Any] = []
    value = _json(value)
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized in keys:
                found.append(item)
            found.extend(_walk(item, keys))
    elif isinstance(value, list):
        for item in value:
            found.extend(_walk(item, keys))
    return found


def _content_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Expose best-effort prompt/reply fields while retaining raw ``other``."""
    other = _json(row.get("other"))
    prompt_keys = {"prompt", "request", "requestbody", "messages", "input", "instructions"}
    reply_keys = {"response", "reply", "responsebody", "completion", "output", "choices", "data"}
    prompts = _walk(other, prompt_keys)
    replies = _walk(other, reply_keys)
    row["prompt"] = _text(prompts[0] if prompts else row.get("prompt", ""))
    row["reply"] = _text(replies[0] if replies else row.get("reply", ""))
    row["other_json"] = other
    return row


class Database:
    """Tiny read-only adapter for SQLite and PostgreSQL New API databases."""

    def __init__(self, url: str):
        self.url = url.strip()
        self.kind = "demo" if not self.url else ("postgres" if self.url.startswith(("postgres://", "postgresql://")) else "sqlite")
        self.path = self._sqlite_path(self.url) if self.kind == "sqlite" else ""
        self._columns: set[str] | None = None

    @staticmethod
    def _sqlite_path(url: str) -> str:
        if not url:
            return ""
        if url.startswith("sqlite:///"):
            return url[10:]
        if url.startswith("sqlite://"):
            return url[9:]
        return url

    @contextmanager
    def connection(self) -> Iterator[Any]:
        if self.kind == "demo":
            yield None
            return
        if self.kind == "sqlite":
            connection = sqlite3.connect(self.path)
            connection.row_factory = sqlite3.Row
            try:
                yield connection
            finally:
                connection.close()
            return
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - depends on deployment
            raise RuntimeError("PostgreSQL requires psycopg[binary]; run pip install -r requirements.txt") from exc
        connection = psycopg.connect(self.url, autocommit=True)
        try:
            yield connection
        finally:
            connection.close()

    def columns(self) -> set[str]:
        if self.kind == "demo":
            return {"id", "created_at", "type", "content", "model_name", "username", "token_name", "other"}
        if self._columns is not None:
            return self._columns
        with self.connection() as conn:
            if self.kind == "sqlite":
                rows = conn.execute("PRAGMA table_info(logs)").fetchall()
                self._columns = {str(row[1]) for row in rows}
            else:
                rows = conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name='logs'").fetchall()
                self._columns = {str(row[0]) for row in rows}
        if not self._columns:
            raise RuntimeError("logs 表不存在，或数据库连接指向了错误的 New API 数据库")
        return self._columns

    def _demo_rows(self) -> list[dict[str, Any]]:
        return [{
            "id": 1, "created_at": int(datetime.now(tz=timezone.utc).timestamp()), "type": 2,
            "content": "演示记录：请配置 NEWAPI_DB_URL", "username": "demo", "token_name": "demo-token",
            "model_name": "demo-model", "prompt_tokens": 12, "completion_tokens": 8, "quota": 20,
            "other": {"prompt": [{"role": "user", "content": "你好，New API"}], "response": {"content": "你好！这是演示回复。"}},
        }]

    def query(self, *, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE, q: str = "", field: str = "all", model: str = "", username: str = "", token_name: str = "", log_type: str = "") -> dict[str, Any]:
        page = max(1, page)
        page_size = max(1, min(MAX_PAGE_SIZE, page_size))
        if self.kind == "demo":
            rows = self._demo_rows()
            rows = [r for r in rows if self._matches(r, q, field, model, username, token_name, log_type)]
            return self._result(rows, page, page_size)
        columns = self.columns()
        selected = [c for c in ("id", "user_id", "created_at", "type", "content", "username", "token_name", "model_name", "quota", "prompt_tokens", "completion_tokens", "use_time", "is_stream", "channel_id", "channel_name", "token_id", "group", "ip", "request_id", "upstream_request_id", "other") if c in columns]
        where: list[str] = []
        values: list[Any] = []
        if model and "model_name" in columns:
            where.append(self._like("model_name")); values.append(f"%{model}%")
        if username and "username" in columns:
            where.append(self._like("username")); values.append(f"%{username}%")
        if token_name and "token_name" in columns:
            where.append(self._like("token_name")); values.append(f"%{token_name}%")
        if log_type and "type" in columns:
            where.append("type = " + self._placeholder()); values.append(int(log_type))
        if q:
            searchable = {"prompt": ["other", "content"], "response": ["other", "content"], "content": ["content"], "model": ["model_name"], "all": selected}
            qcols = [c for c in searchable.get(field, selected) if c in columns]
            if qcols:
                needle = f"%{q}%"
                where.append("(" + " OR ".join(self._like(c) for c in qcols) + ")")
                values.extend([needle] * len(qcols))
        condition = (" WHERE " + " AND ".join(where)) if where else ""
        ph = self._placeholder()
        order = "created_at DESC, id DESC" if "created_at" in columns else "id DESC"
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM logs{condition}", values)
            total = int(cursor.fetchone()[0])
            offset = (page - 1) * page_size
            params = values + [page_size, offset]
            cursor.execute(f"SELECT {', '.join(selected)} FROM logs{condition} ORDER BY {order} LIMIT {ph} OFFSET {ph}", params)
            rows = [dict(row) if isinstance(row, sqlite3.Row) else dict(zip(selected, row)) for row in cursor.fetchall()]
        # Prompt/response filtering is also applied after JSON decoding because
        # PostgreSQL and old SQLite installations may store ``other`` differently.
        rows = [_content_fields(row) for row in rows]
        if q and field in {"prompt", "response"}:
            rows = [row for row in rows if q.casefold() in _text(row.get(field)).casefold()]
        return {"items": rows, "total": total, "page": page, "page_size": page_size, "database": self.kind}

    def get(self, log_id: str) -> dict[str, Any] | None:
        if self.kind == "demo":
            row = self._demo_rows()[0] if str(log_id) == "1" else None
            return _content_fields(row) if row else None
        columns = self.columns()
        selected = [c for c in ("id", "user_id", "created_at", "type", "content", "username", "token_name", "model_name", "quota", "prompt_tokens", "completion_tokens", "use_time", "is_stream", "channel_id", "channel_name", "token_id", "group", "ip", "request_id", "upstream_request_id", "other") if c in columns]
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT {', '.join(selected)} FROM logs WHERE id = {self._placeholder()}", [log_id])
            row = cursor.fetchone()
            if row is None:
                return None
            return _content_fields(dict(row) if isinstance(row, sqlite3.Row) else dict(zip(selected, row)))

    def _placeholder(self) -> str:
        return "?" if self.kind == "sqlite" else "%s"

    def _like(self, column: str) -> str:
        return f"{column} LIKE {self._placeholder()}" + (" COLLATE NOCASE" if self.kind == "sqlite" else " ILIKE" if False else "")

    @staticmethod
    def _matches(row: dict[str, Any], q: str, field: str, model: str, username: str, token_name: str, log_type: str) -> bool:
        if model and model.casefold() not in _text(row.get("model_name")).casefold(): return False
        if username and username.casefold() not in _text(row.get("username")).casefold(): return False
        if token_name and token_name.casefold() not in _text(row.get("token_name")).casefold(): return False
        if log_type and str(row.get("type")) != log_type: return False
        if q:
            row = _content_fields(dict(row))
            value = row.get(field) if field in {"prompt", "response", "content", "model"} else row
            if q.casefold() not in _text(value).casefold(): return False
        return True

    @staticmethod
    def _result(rows: list[dict[str, Any]], page: int, page_size: int) -> dict[str, Any]:
        total = len(rows)
        start = (page - 1) * page_size
        return {"items": [_content_fields(dict(r)) for r in rows[start:start + page_size]], "total": total, "page": page, "page_size": page_size, "database": "demo"}


db = Database(DB_URL)
app = FastAPI(title="NewAPI Logs Reader")
public = Path(__file__).with_name("public")
if public.is_dir():
    app.mount("/static", StaticFiles(directory=public), name="static")


@app.get("/healthz")
def health() -> dict[str, Any]:
    try:
        columns = sorted(db.columns())
        return {"ok": True, "database": db.kind, "table": "logs", "columns": columns}
    except Exception as exc:
        return {"ok": False, "database": db.kind, "error": str(exc)}


@app.get("/api/logs")
def logs(page: int = Query(1, ge=1), page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE), q: str = Query("", max_length=500), field: str = Query("all"), model: str = Query("", max_length=200), username: str = Query("", max_length=200), token_name: str = Query("", max_length=200), log_type: str = Query("")) -> dict[str, Any]:
    try:
        return db.query(page=page, page_size=page_size, q=q, field=field, model=model, username=username, token_name=token_name, log_type=log_type)
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@app.get("/api/logs/{log_id}")
def log_detail(log_id: str) -> dict[str, Any]:
    try:
        row = db.get(log_id)
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    if row is None:
        raise HTTPException(404, "日志不存在")
    return row


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    html = public / "index.html"
    return html.read_text(encoding="utf-8") if html.exists() else "<h1>NewAPI Logs Reader</h1>"


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host=HOST, port=PORT, reload=False)



