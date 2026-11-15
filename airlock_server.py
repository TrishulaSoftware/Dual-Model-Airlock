"""
Dual-Model-Airlock Threaded HTTP Server (v2.0.0)
Zero-dependency, multi-threaded REST gateway for dispatching tasks and cross-model reviews
between Google Gemini, Claude Code CLI, Codex, and Aider.
Part of the Trishula Sovereign Architecture — https://trishulasoftware.github.io
"""

import hmac
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

try:
    from .airlock_bridge import AirlockBridge
    from .airlock_core import AirlockSecurityPolicy, AirlockTask
except ImportError:
    from airlock_bridge import AirlockBridge
    from airlock_core import AirlockSecurityPolicy, AirlockTask

MAX_BODY_BYTES = 1_048_576  # 1 MiB payload ceiling


class AirlockRequestHandler(BaseHTTPRequestHandler):
    bridge: Optional[AirlockBridge] = None
    api_key: Optional[str] = None
    allowed_origin: str = "http://127.0.0.1"

    def _send_json(self, status_code: int, data: dict) -> None:
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", self.allowed_origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Airlock-Key")
        self.end_headers()
        self.wfile.write(body)

    def _check_auth(self) -> bool:
        expected = self.api_key or os.environ.get("AIRLOCK_API_KEY")
        if not expected:
            return True

        auth_header = self.headers.get("Authorization", "")
        token = ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
        elif self.headers.get("X-Airlock-Key"):
            token = self.headers.get("X-Airlock-Key", "").strip()

        if not token or not hmac.compare_digest(token, expected):
            self._send_json(401, {"error": "Unauthorized: valid Bearer token or X-Airlock-Key required"})
            return False
        return True

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", self.allowed_origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Airlock-Key")
        self.end_headers()

    def do_GET(self) -> None:
        url = urllib.parse.urlparse(self.path)
        path = url.path.rstrip("/")

        if path in ("", "/health"):
            workers = self.bridge.discover_workers() if self.bridge else {}
            self._send_json(
                200,
                {
                    "status": "online",
                    "service": "Dual-Model-Airlock",
                    "version": "2.0.0",
                    "claude_available": bool(self.bridge._resolve_claude_binary()) if self.bridge else False,
                    "auth_enabled": bool(self.api_key or os.environ.get("AIRLOCK_API_KEY")),
                    "workers": workers,
                },
            )
            return

        if not self._check_auth():
            return

        if path == "/v1/workers":
            workers = self.bridge.discover_workers() if self.bridge else {}
            self._send_json(200, {"workers": workers})
            return

        if path.startswith("/v1/task/"):
            task_id = path.replace("/v1/task/", "").strip()
            res = self.bridge.get_status(task_id) if self.bridge else None
            if not res:
                self._send_json(404, {"error": f"Task '{task_id}' not found"})
            else:
                self._send_json(200, res.to_dict())
            return

        if path == "/v1/tasks":
            all_res = {tid: r.to_dict() for tid, r in self.bridge.results.items()} if self.bridge else {}
            self._send_json(200, {"tasks": all_res})
            return

        self._send_json(404, {"error": "Endpoint not found"})

    def do_POST(self) -> None:
        if not self._check_auth():
            return

        url = urllib.parse.urlparse(self.path)
        path = url.path.rstrip("/")

        try:
            content_len = int(self.headers.get("Content-Length", 0))
        except ValueError:
            content_len = 0

        if content_len > MAX_BODY_BYTES:
            self._send_json(413, {"error": f"Payload exceeds maximum size of {MAX_BODY_BYTES} bytes"})
            return

        body = self.rfile.read(content_len).decode("utf-8", errors="replace") if content_len > 0 else "{}"
        try:
            payload = json.loads(body)
        except Exception:
            self._send_json(400, {"error": "Invalid JSON body"})
            return

        if not self.bridge:
            self._send_json(500, {"error": "Bridge not initialized"})
            return

        if path == "/v1/dispatch":
            prompt = payload.get("prompt")
            if not prompt:
                self._send_json(400, {"error": "'prompt' field is required"})
                return

            task = AirlockTask.from_dict(
                {
                    "task_id": payload.get("task_id"),
                    "prompt": prompt,
                    "commander": payload.get("commander", "gemini"),
                    "worker": payload.get("worker", "claude"),
                    "cwd": payload.get("cwd"),
                    "timeout_sec": payload.get("timeout_sec", 120),
                    "parameters": payload.get("parameters", {}),
                }
            )
            is_async = bool(payload.get("async", False))
            result = self.bridge.execute_task(task, async_run=is_async)
            http_code = 403 if result.status.value == "rejected" else 200
            self._send_json(
                http_code,
                {
                    "task_id": task.task_id,
                    "status": result.status.value,
                    "result": result.to_dict(),
                },
            )
            return

        if path == "/v1/review":
            target = payload.get("target")
            if not target:
                self._send_json(400, {"error": "'target' field (file path or code snippet) is required"})
                return

            result = self.bridge.execute_review(
                target=target,
                commander=payload.get("commander", "gemini-2.5-pro"),
                worker=payload.get("worker", "claude"),
                cwd=payload.get("cwd"),
                timeout_sec=int(payload.get("timeout_sec", 180)),
            )
            self._send_json(
                200,
                {
                    "task_id": result.task_id,
                    "status": result.status.value,
                    "result": result.to_dict(),
                },
            )
            return

        if path.startswith("/v1/cancel/"):
            task_id = path.replace("/v1/cancel/", "").strip()
            if self.bridge.cancel_task(task_id):
                self._send_json(200, {"status": "cancelled", "task_id": task_id})
            else:
                self._send_json(400, {"error": f"Unable to cancel task '{task_id}'"})
            return

        self._send_json(404, {"error": "Endpoint not found"})

    def log_message(self, format: str, *args) -> None:
        sys.stderr.write(f"[AIRLOCK_SERVER] {args[0]} - {args[1]}\n")


def start_server(
    port: int = 9090,
    host: str = "127.0.0.1",
    api_key: Optional[str] = None,
    policy: Optional[AirlockSecurityPolicy] = None,
    audit_log_path: Optional[str] = None,
    allowed_origin: str = "http://127.0.0.1",
) -> ThreadingHTTPServer:
    bridge = AirlockBridge(policy=policy, audit_log_path=audit_log_path)

    class BoundHandler(AirlockRequestHandler):
        pass

    BoundHandler.bridge = bridge
    BoundHandler.api_key = api_key
    BoundHandler.allowed_origin = allowed_origin

    server = ThreadingHTTPServer((host, port), BoundHandler)
    print(f"[AIRLOCK] Threaded REST Server v2.0.0 listening on http://{host}:{port}")
    return server


if __name__ == "__main__":
    srv = start_server(port=9090)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[AIRLOCK] Server terminated.")
