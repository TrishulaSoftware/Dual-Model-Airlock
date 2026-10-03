"""
Dual-Model-Airlock HTTP Server
Lightweight, zero-dependency REST server for dispatching tasks between Gemini and Claude Code.
"""

import sys
import json
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

from .airlock_core import AirlockTask, TaskStatus
from .airlock_bridge import AirlockBridge


class AirlockRequestHandler(BaseHTTPRequestHandler):
    bridge: Optional[AirlockBridge] = None

    def _send_json(self, status_code: int, data: dict):
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path = url.path.rstrip("/")

        if path in ("", "/health"):
            self._send_json(200, {
                "status": "online",
                "service": "Dual-Model-Airlock",
                "version": "1.0.0",
                "claude_available": bool(self.bridge._resolve_claude_binary()) if self.bridge else False
            })
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

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        path = url.path.rstrip("/")

        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
        try:
            payload = json.loads(body)
        except Exception:
            self._send_json(400, {"error": "Invalid JSON body"})
            return

        if path == "/v1/dispatch":
            prompt = payload.get("prompt")
            if not prompt:
                self._send_json(400, {"error": "'prompt' field is required"})
                return

            task = AirlockTask(
                prompt=prompt,
                commander=payload.get("commander", "gemini"),
                worker=payload.get("worker", "claude"),
                cwd=payload.get("cwd"),
                timeout_sec=payload.get("timeout_sec", 120),
                parameters=payload.get("parameters", {})
            )
            is_async = payload.get("async", False)

            if not self.bridge:
                self._send_json(500, {"error": "Bridge not initialized"})
                return

            result = self.bridge.execute_task(task, async_run=is_async)
            self._send_json(200, {
                "task_id": task.task_id,
                "status": result.status.value,
                "result": result.to_dict()
            })
            return

        if path.startswith("/v1/cancel/"):
            task_id = path.replace("/v1/cancel/", "").strip()
            if self.bridge and self.bridge.cancel_task(task_id):
                self._send_json(200, {"status": "cancelled", "task_id": task_id})
            else:
                self._send_json(400, {"error": f"Unable to cancel task '{task_id}'"})
            return

        self._send_json(404, {"error": "Endpoint not found"})

    def log_message(self, format, *args):
        # Clean logging format
        sys.stderr.write(f"[AIRLOCK_SERVER] {args[0]} - {args[1]}\n")


def start_server(port: int = 9090, host: str = "127.0.0.1") -> HTTPServer:
    bridge = AirlockBridge()
    AirlockRequestHandler.bridge = bridge
    server = HTTPServer((host, port), AirlockRequestHandler)
    print(f"[AIRLOCK] Server listening on http://{host}:{port}")
    return server


if __name__ == "__main__":
    server = start_server(port=9090)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[AIRLOCK] Server terminated.")
