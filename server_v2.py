"""NewAPI Logs Reader v2 backend."""
from __future__ import annotations
import base64, binascii, csv, hashlib, hmac, io, json, os, re, secrets, sqlite3, time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

PORT = int(os.getenv("PORT", os.getenv("APP_PORT", "3333")))
HOST = os.getenv("HOST", os.getenv("APP_HOST", "0.0.0.0"))
MAIN_DSN = os.getenv("SQL_DSN", os.getenv("DB_DSN", "")).strip()
LOG_DSN = os.getenv("LOG_SQL_DSN", os.getenv("LOG_DB_DSN", "")).strip() or MAIN_DSN
ROOT_USERNAME = os.getenv("ROOT_USERNAME", os.getenv("NEWAPI_ROOT_USERNAME", "root"))
SESSION_SECRET = os.getenv("SESSION_SECRET", "").strip() or secrets.token_urlsafe(32)
SESSION_TTL = max(300, int(os.getenv("SESSION_TTL", "43200")))
SESSION_COOKIE = os.getenv("SESSION_COOKIE", "newapi_logs_session")
SESSION_SECURE = os.getenv("SESSION_SECURE", "0").lower() in {"1", "true", "yes", "on"}
MAX_PAGE = 200
MAX_EXPORT = max(100, int(os.getenv("MAX_EXPORT_ROWS", "10000")))

def j(v: Any) -> Any:
    if v is None or isinstance(v, (dict, list, int, float, bool)): return v
    if isinstance(v, bytes): v = v.decode("utf-8", "replace")
    if isinstance(v, str):
        try: return json.loads(v)
        except (TypeError, ValueError): return v
    return str(v)

def txt(v: Any) -> str:
    v = j(v)
    return v if isinstance(v, str) else ("" if v is None else json.dumps(v, ensure_ascii=False, indent=2, default=str))

def walk(v: Any, keys: set[str]) -> list[Any]:
    out: list[Any] = []; v = j(v)
    if isinstance(v, dict):
        for k, x in v.items():
            if re.sub(r"[^a-z0-9]", "", str(k).lower()) in keys: out.append(x)
            out.extend(walk(x, keys))
    elif isinstance(v, list):
        for x in v: out.extend(walk(x, keys))
    return out

def enrich(row: dict[str, Any]) -> dict[str, Any]:
    other = j(row.get("other"))
    p = walk(other, {"prompt","request","requestbody","messages","input","instructions"})
    r = walk(other, {"response","reply","responsebody","completion","output","choices","data"})
    row["prompt"] = txt(p[0] if p else row.get("prompt", ""))
    row["reply"] = txt(r[0] if r else row.get("reply", ""))
    row["response"] = row["reply"]
    row["other_json"] = other
    return row

SKEY = re.compile(r"(?:^|[_-])(password|passwd|secret|api[-_]?key|access[-_]?token|refresh[-_]?token|authorization|cookie|set[-_]?cookie|private[-_]?key|client[-_]?secret|jwt)(?:$|[_-])", re.I)
SINLINE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}|\b(sk-[A-Za-z0-9_-]{8,})\b|\b(api[_-]?key\s*[:=]\s*)[^\s,;]+|\b(password\s*[:=]\s*)[^\s,;]+")
def mask(v: Any, key: str | None = None) -> Any:
    if key and SKEY.search(key): return "[REDACTED]"
    if isinstance(v, dict): return {str(k): mask(x, str(k)) for k, x in v.items()}
    if isinstance(v, list): return [mask(x, key) for x in v]
    if isinstance(v, str):
        def sub(m: re.Match[str]) -> str:
            s = m.group(0)
            if s.lower().startswith("bearer"): return "Bearer [REDACTED]"
            if ":" in s or "=" in s: return re.sub(r"([:=]\s*)[^\s,;]+$", r"\1[REDACTED]", s)
            return "[REDACTED]"
        return SINLINE.sub(sub, v)
    return v
def visible(row: dict[str, Any], reveal: bool = False) -> dict[str, Any]:
    row = enrich(dict(row))
    return row if reveal else {k: mask(v, k) for k, v in row.items()}

class DB:
    def __init__(self, dsn: str, label: str):
        self.dsn, self.label = dsn.strip(), label
        self.kind = "demo" if not self.dsn else ("postgres" if self.dsn.startswith(("postgres://","postgresql://")) else "sqlite")
        self.path = self.dsn[10:] if self.dsn.startswith("sqlite:///") else self.dsn[9:] if self.dsn.startswith("sqlite://") else ""
        self._cols: dict[str, set[str]] = {}
    @contextmanager
    def conn(self) -> Iterator[Any]:
        if self.kind == "demo": yield None; return
        if self.kind == "sqlite":
            c = sqlite3.connect(self.path); c.row_factory = sqlite3.Row
            try: yield c
            finally: c.close()
            return
        try: import psycopg
        except ImportError as e: raise RuntimeError("Install psycopg[binary]") from e
        c = psycopg.connect(self.dsn, autocommit=True)
        try: yield c
        finally: c.close()
    def cols(self, table: str) -> set[str]:
        if self.kind == "demo": return {"id","username","password","role","status"} if table == "users" else {"id","created_at","type","content","model_name","username","token_name","other"}
        if table in self._cols: return self._cols[table]
        with self.conn() as c:
            if self.kind == "sqlite": rows = c.execute(f"PRAGMA table_info({table})").fetchall(); out = {str(x[1]) for x in rows}
            else:
                rows = c.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s AND table_schema=ANY(current_schemas(false))",[table]).fetchall(); out = {str(x[0]) for x in rows}
        self._cols[table] = out
        return out
    def ph(self) -> str: return "?" if self.kind == "sqlite" else "%s"
    def ident(self, name: str) -> str:
        return '"' + name.replace('"', '""') + '"'
    def cast(self, col: str) -> str:
        quoted = self.ident(col)
        return f"CAST({quoted} AS TEXT)"
    def op(self, col: str, regex: bool) -> str:
        if regex:
            return f"{self.cast(col)} LIKE {self.ph()}" if self.kind == "sqlite" else f"{self.cast(col)} ~* {self.ph()}"
        return f"{self.cast(col)} LIKE {self.ph()}" + (" COLLATE NOCASE" if self.kind == "sqlite" else "")
    def demo_rows(self) -> list[dict[str, Any]]:
        return [{"id":1,"created_at":int(datetime.now(tz=timezone.utc).timestamp()),"type":2,"content":"演示记录，请配置 DB_DSN 与 LOG_DB_DSN","username":"demo","token_name":"demo-token","model_name":"demo-model","prompt_tokens":12,"completion_tokens":8,"quota":20,"other":{"prompt":[{"role":"user","content":"你好，New API"}],"response":{"content":"演示回复"}}}]
    def matches(self, row: dict[str, Any], q: str, field: str, model: str, user: str, token: str, typ: str, regex: bool) -> bool:
        def hit(v: Any, needle: str) -> bool:
            try: return re.search(needle, txt(v), re.I) is not None if regex else needle.casefold() in txt(v).casefold()
            except re.error: return False
        return (not model or hit(row.get("model_name"), model)) and (not user or hit(row.get("username"), user)) and (not token or hit(row.get("token_name"), token)) and (not typ or str(row.get("type")) == typ) and (not q or hit(enrich(dict(row)).get(field) if field in {"prompt","response","content","model"} else row, q))
    def query(self, page=1, page_size=50, q="", field="all", model="", username="", token_name="", log_type="", regex=False, after_id="", reveal=False) -> dict[str, Any]:
        page, page_size = max(1, page), min(MAX_EXPORT, max(1, page_size))
        if regex and q:
            try:
                re.compile(q)
            except re.error as e:
                raise ValueError(f"正则表达式无效：{e}") from e
        if self.kind == "demo":
            rows = [x for x in self.demo_rows() if self.matches(x, q, field, model, username, token_name, log_type, regex)]
            total, start = len(rows), (page - 1) * page_size
            items = [visible(x, reveal) for x in rows[start:start + page_size]]
            for item in items:
                item["_source"] = self.label
                item["record_key"] = f"{self.label}:{item.get('id')}"
            return {"items": items, "total": total, "page": page, "page_size": page_size, "database": "demo", "database_label": self.label, "generated_at": datetime.now(tz=timezone.utc).isoformat(), "latest_id": items[0].get("id") if items else None, "poll_after_ms": 5000}
        cols = self.cols("logs")
        selected = [x for x in ("id", "user_id", "created_at", "type", "content", "username", "token_name", "model_name", "quota", "prompt_tokens", "completion_tokens", "use_time", "is_stream", "channel_id", "channel_name", "token_id", "group", "ip", "request_id", "upstream_request_id", "other", "response_payload", "request_payload") if x in cols]
        if not selected:
            raise RuntimeError("logs 表不存在")
        where: list[str] = []
        vals: list[Any] = []
        for val, col in ((model, "model_name"), (username, "username"), (token_name, "token_name")):
            if val and col in cols:
                where.append(self.op(col, regex))
                vals.append(val if regex else f"%{val}%")
        if log_type and "type" in cols:
            where.append(self.ident("type") + "=" + self.ph())
            vals.append(int(log_type))
        if after_id and "id" in cols:
            try:
                where.append(self.ident("id") + ">" + self.ph())
                vals.append(int(after_id))
            except ValueError:
                pass
        if q:
            fmap = {"prompt": ["other", "request_payload", "content"], "response": ["other", "response_payload", "content"], "content": ["content"], "model": ["model_name"], "all": selected}
            qcols = [x for x in fmap.get(field, selected) if x in cols]
            if qcols:
                where.append("(" + " OR ".join(self.op(x, regex) for x in qcols) + ")")
                vals.extend([q if regex else f"%{q}%"] * len(qcols))
        table = self.ident("logs")
        cond = " WHERE " + " AND ".join(where) if where else ""
        ph = self.ph()
        order = self.ident("created_at") + " DESC, " + self.ident("id") + " DESC" if "created_at" in cols else self.ident("id") + " DESC"
        select_sql = ", ".join(self.ident(x) for x in selected)
        with self.conn() as c:
            cur = c.cursor()
            cur.execute(f"SELECT COUNT(*) FROM {table}{cond}", vals)
            total = int(cur.fetchone()[0])
            cur.execute(f"SELECT {select_sql} FROM {table}{cond} ORDER BY {order} LIMIT {ph} OFFSET {ph}", vals + [page_size, (page - 1) * page_size])
            fetched = cur.fetchall()
            rows = [dict(x) if isinstance(x, sqlite3.Row) else dict(zip(selected, x)) for x in fetched]
        items = [visible(x, reveal) for x in rows]
        for item in items:
            item["_source"] = self.label
            item["record_key"] = f"{self.label}:{item.get('id')}"
        return {"items": items, "total": total, "page": page, "page_size": page_size, "database": self.kind, "database_label": self.label, "generated_at": datetime.now(tz=timezone.utc).isoformat(), "latest_id": items[0].get("id") if items else None, "poll_after_ms": 5000}
    def get(self, ident: str, reveal=False) -> dict[str, Any] | None:
        if self.kind == "demo":
            return visible(self.demo_rows()[0], reveal) if ident == "1" else None
        cols = self.cols("logs")
        selected = [x for x in ("id", "user_id", "created_at", "type", "content", "username", "token_name", "model_name", "quota", "prompt_tokens", "completion_tokens", "use_time", "is_stream", "channel_id", "channel_name", "token_id", "group", "ip", "request_id", "upstream_request_id", "other", "response_payload", "request_payload") if x in cols]
        with self.conn() as c:
            cur = c.cursor()
            cur.execute(f"SELECT {', '.join(self.ident(x) for x in selected)} FROM {self.ident('logs')} WHERE {self.ident('id')}={self.ph()}", [ident])
            row = cur.fetchone()
        return None if row is None else visible(dict(row) if isinstance(row, sqlite3.Row) else dict(zip(selected, row)), reveal)

    def user(self, name: str) -> dict[str,Any] | None:
        if self.kind=="demo":
            p=os.getenv("ROOT_PASSWORD",""); return {"username":name,"password":p} if p else None
        cols=self.cols("users")
        if not {"username","password"}.issubset(cols): raise RuntimeError("users 表缺少 username/password")
        sel=[x for x in ("id", "username", "password", "role", "status") if x in cols]
        with self.conn() as c:
            cur = c.cursor()
            cur.execute(f"SELECT {', '.join(self.ident(x) for x in sel)} FROM {self.ident('users')} WHERE LOWER({self.ident('username')})=LOWER({self.ph()}) LIMIT 1", [name])
            row = cur.fetchone()
        return None if row is None else (dict(row) if isinstance(row,sqlite3.Row) else dict(zip(sel,row)))
    def status(self, table: str) -> dict[str,Any]:
        try: return {"ok":bool(self.cols(table)),"database":self.kind,"label":self.label,"table":table,"columns":sorted(self.cols(table))}
        except Exception as e: return {"ok":False,"database":self.kind,"label":self.label,"table":table,"error":str(e)}

def combined_logs(page=1, page_size=50, q="", field="all", model="", username="", token_name="", log_type="", regex=False, after_id="", reveal=False, source="all") -> dict[str, Any]:
    source = source.lower()
    if source in {"primary", "main"}:
        targets = [main_db]
    elif source in {"logs", "log"}:
        targets = [log_db]
    elif MAIN_DSN and LOG_DSN and MAIN_DSN == LOG_DSN:
        targets = [log_db]
    else:
        targets = [main_db, log_db]
    fetch_size = min(MAX_EXPORT, max(page * page_size, page_size))
    results = [db.query(1, fetch_size, q, field, model, username, token_name, log_type, regex, after_id, reveal) for db in targets]
    merged = []
    total = 0
    for result in results:
        total += int(result.get("total", 0))
        merged.extend(result.get("items", []))
    def sort_key(item):
        created = item.get("created_at")
        try:
            created = int(created)
        except (TypeError, ValueError):
            created = 0
        ident = str(item.get("id", ""))
        try:
            ident_key = int(ident)
        except ValueError:
            ident_key = 0
        return (created, ident_key, str(item.get("_source", "")))
    merged.sort(key=sort_key, reverse=True)
    start = (page - 1) * page_size
    items = merged[start:start + page_size]
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "database": "combined" if len(targets) > 1 else targets[0].label,
        "database_label": "主库 + 日志库" if len(targets) > 1 else targets[0].label,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "latest_id": items[0].get("id") if items else None,
        "poll_after_ms": 5000,
        "sources": [db.label for db in targets],
    }

main_db, log_db = DB(MAIN_DSN,"primary"), DB(LOG_DSN,"logs")
app=FastAPI(title="NewAPI Logs Reader")
public=Path(__file__).with_name("public_v2")
if public.is_dir():
    app.mount("/static",StaticFiles(directory=public),name="static")
    assets = public / "assets"
    if assets.is_dir(): app.mount("/assets",StaticFiles(directory=assets),name="assets")

def token_for(user: str) -> str:
    p=base64.urlsafe_b64encode(f"{user}|{int(time.time())}".encode()).decode().rstrip("=")
    return p+"."+hmac.new(SESSION_SECRET.encode(),p.encode(),hashlib.sha256).hexdigest()
def session_user(req: Request) -> str | None:
    try:
        p,s=req.cookies.get(SESSION_COOKIE,"").split(".",1); good=hmac.new(SESSION_SECRET.encode(),p.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(s,good): return None
        user,ts=base64.urlsafe_b64decode(p+"="*(-len(p)%4)).decode().rsplit("|",1)
        return user if int(time.time())-int(ts)<=SESSION_TTL else None
    except (ValueError,TypeError,UnicodeDecodeError,binascii.Error,OverflowError): return None
def auth(req: Request) -> str:
    user=session_user(req)
    if not user: raise HTTPException(401,"请先使用 NewAPI Root 用户登录")
    return user
class Login(BaseModel):
    password: str = Field(min_length=1,max_length=512)
    username: str = Field(default=ROOT_USERNAME,min_length=1,max_length=120)
def verify(password: str, stored: str) -> bool:
    if stored.startswith(("$2a$","$2b$","$2y$")):
        try:
            import bcrypt
            return bool(bcrypt.checkpw(password.encode(),stored.encode()))
        except (ImportError,ValueError,RuntimeError): return False
    if stored.startswith("$argon2"):
        try:
            from argon2 import PasswordHasher
            return bool(PasswordHasher().verify(stored,password))
        except (ImportError,ValueError,RuntimeError): return False
    return bool(stored) and hmac.compare_digest(password,stored)

@app.get("/api/health")
def health_alias() -> dict[str,Any]:
    return health()

@app.get("/healthz")
def health() -> dict[str,Any]:
    p,l=main_db.status("users"),log_db.status("logs")
    payload_capable = False
    audit_capable = False
    channels_capable = False
    try:
        payload_capable = bool(log_db.cols("payload_logs"))
        audit_capable = bool(log_db.cols("audit_logs"))
        channels_capable = bool(main_db.cols("channels"))
    except Exception:
        pass
    return {
        "ok": bool(p["ok"] and l["ok"]),
        "primary": p,
        "logs": l,
        "same_database": bool(MAIN_DSN and LOG_DSN and MAIN_DSN == LOG_DSN),
        "capabilities": {"payload_logs": payload_capable, "audit_logs": audit_capable, "channels": channels_capable},
        "auth": "root-user",
    }
@app.get("/api/auth/status")
def auth_status(req: Request) -> dict[str,Any]:
    user=session_user(req); return {"authenticated":bool(user),"username":user,"expires_in":SESSION_TTL if user else 0}
@app.post("/api/auth/login")
def login(payload: Login, response: Response) -> dict[str,Any]:
    try: user=main_db.user(payload.username)
    except Exception as e: raise HTTPException(503,str(e)) from e
    if not user or int(user.get("role", 0) or 0) != 100 or int(user.get("status", 0) or 0) != 1 or not verify(payload.password, str(user.get("password", ""))):
        raise HTTPException(401, "Root 用户名或密码错误")
    name=str(user.get("username") or payload.username); response.set_cookie(SESSION_COOKIE,token_for(name),max_age=SESSION_TTL,httponly=True,samesite="lax",secure=SESSION_SECURE,path="/")
    return {"ok":True,"username":name,"user":{"username":name},"expires_in":SESSION_TTL}
@app.get("/api/auth/me")
def auth_me(req: Request) -> dict[str,Any]:
    user=session_user(req)
    if not user: raise HTTPException(401,"请先登录")
    return {"user":{"username":user},"username":user}

@app.post("/api/auth/logout")
def logout(response: Response) -> dict[str,bool]:
    response.delete_cookie(SESSION_COOKIE,path="/"); return {"ok":True}

@app.get("/api/logs")
def logs(page:int=Query(1,ge=1),page_size:int=Query(50,ge=1,le=MAX_PAGE),offset:int|None=Query(None,ge=0),limit:int|None=Query(None,ge=1,le=MAX_PAGE),q:str=Query("",max_length=1000),field:str=Query("all"),model:str=Query("",max_length=200),username:str=Query("",max_length=200),status:str=Query(""),token_name:str=Query("",max_length=200),log_type:str=Query(""),regex:bool=Query(False),after_id:str=Query("",max_length=80),reveal:bool=Query(False),source:str=Query("all"),_user:str=Depends(auth)) -> dict[str,Any]:
    if offset is not None: page = offset // (limit or page_size) + 1
    if limit is not None: page_size = limit
    if status and not log_type:
        log_type = {"error": "5", "success": "2", "pending": "0"}.get(status, status if status.isdigit() else "")
    try: return combined_logs(page,page_size,q,field,model,username,token_name,log_type,regex,after_id,reveal,source)
    except ValueError as e: raise HTTPException(400,str(e)) from e
    except Exception as e: raise HTTPException(500,str(e)) from e
@app.get("/api/logs/{ident}")
def detail(ident:str,reveal:bool=Query(False),_user:str=Depends(auth)) -> dict[str,Any]:
    db = log_db
    raw_ident = ident
    if ":" in ident:
        source_name, raw_ident = ident.split(":", 1)
        db = main_db if source_name in {"primary", "main"} else log_db
    try: row=db.get(raw_ident,reveal)
    except Exception as e: raise HTTPException(500,str(e)) from e
    if row is None: raise HTTPException(404,"日志不存在")
    return row
@app.get("/api/export")
def export(format:str=Query("json",pattern="^(json|csv)$"),q:str=Query("",max_length=1000),field:str=Query("all"),model:str=Query("",max_length=200),username:str=Query("",max_length=200),token_name:str=Query("",max_length=200),log_type:str=Query(""),regex:bool=Query(False),reveal:bool=Query(False),source:str=Query("all"),_user:str=Depends(auth)) -> Response:
    try: result=combined_logs(1,MAX_EXPORT,q,field,model,username,token_name,log_type,regex,"",reveal,source)
    except ValueError as e: raise HTTPException(400,str(e)) from e
    except Exception as e: raise HTTPException(500,str(e)) from e
    stamp=datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S"); fn=f'attachment; filename="newapi-logs-{stamp}.{format}"'
    if format=="json": return JSONResponse({"items":result["items"],"total":result["total"],"generated_at":result["generated_at"]},headers={"Content-Disposition":fn})
    out=io.StringIO(newline=""); fields=sorted({k for row in result["items"] for k in row}) or ["id"]; w=csv.DictWriter(out,fieldnames=fields); w.writeheader()
    for row in result["items"]: w.writerow({k:txt(row.get(k)) for k in fields})
    return StreamingResponse(iter([out.getvalue().encode("utf-8-sig")]),media_type="text/csv; charset=utf-8",headers={"Content-Disposition":fn})
@app.get("/")
def index() -> HTMLResponse:
    p=public/"index.html"; return HTMLResponse(p.read_text(encoding="utf-8") if p.exists() else "<h1>NewAPI Logs Reader</h1>")
if __name__=="__main__":
    import uvicorn
    uvicorn.run("server_v2:app",host=HOST,port=PORT,reload=False)

