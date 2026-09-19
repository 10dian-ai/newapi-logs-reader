from __future__ import annotations

import http.client
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import capture_store

TARGET_URL = os.getenv("CAPTURE_TARGET", "http://new-api:3000")
TARGET = urlsplit(TARGET_URL)
TARGET_HOST = TARGET.hostname or "new-api"
TARGET_PORT = TARGET.port or (443 if TARGET.scheme == "https" else 3000)
MAX_PROXY_BODY = int(os.getenv("CAPTURE_PROXY_MAX_BODY_BYTES", str(32 * 1024 * 1024)))
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade", "content-length", "host"}


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _authorized(self) -> bool:
        # The proxy never accepts anonymous forwarding. The caller must provide
        # the same NewAPI Authorization/API-key header it would send to port 3000.
        return bool(self.headers.get("Authorization") or self.headers.get("X-Api-Key") or self.headers.get("api-key"))

    def _forward(self):
        if not self._authorized():
            self.send_error(401, "NewAPI Authorization header required")
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > MAX_PROXY_BODY:
            self.send_error(413, "request body too large")
            return
        body = self.rfile.read(length) if length else b""
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_HEADERS}
        headers["Host"] = TARGET_HOST
        connection = http.client.HTTPConnection(TARGET_HOST, TARGET_PORT, timeout=600)
        started = time.time()
        try:
            connection.request(self.command, self.path, body=body, headers=headers)
            upstream = connection.getresponse()
            self.send_response(upstream.status, upstream.reason)
            for key, value in upstream.getheaders():
                if key.lower() not in HOP_HEADERS:
                    self.send_header(key, value)
            self.end_headers()
            captured = bytearray()
            while True:
                chunk = upstream.read(64 * 1024)
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                except BrokenPipeError:
                    break
                if capture_store.status()["enabled"] and len(captured) < capture_store.MAX_BODY:
                    captured.extend(chunk[: capture_store.MAX_BODY - len(captured)])
            session = capture_store.status()
            if session["enabled"] and self.command == "POST":
                request_id = self.headers.get("X-Request-Id", "")
                capture_store.add_record(session["id"], self.command, self.path, body, bytes(captured), upstream.status, int((time.time() - started) * 1000), request_id)
        except Exception as exc:
            try:
                self.send_error(502, str(exc))
            except Exception:
                pass
        finally:
            connection.close()

    def do_GET(self): self._forward()
    def do_POST(self): self._forward()
    def do_PUT(self): self._forward()
    def do_PATCH(self): self._forward()
    def do_DELETE(self): self._forward()
    def log_message(self, *args): return


def start_proxy() -> ThreadingHTTPServer:
    capture_store.init_db()
    port = int(os.getenv("CAPTURE_PROXY_PORT", "3334"))
    server = ThreadingHTTPServer(("0.0.0.0", port), ProxyHandler)
    threading.Thread(target=server.serve_forever, name="capture-proxy", daemon=True).start()
    return server

