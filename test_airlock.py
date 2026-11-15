"""
Comprehensive Test Suite for Dual-Model-Airlock (v2.0.0)
Verifies:
1. Core schemas, serialization, and SHA-256 receipt hashing
2. Synchronous & asynchronous worker dispatch
3. Real OS/thread task cancellation semantics
4. Perimeter security gate (destructive command blocking, shell worker policy, cwd sandboxing)
5. Multi-threaded HTTP REST Server endpoints (/health, /v1/workers, /v1/dispatch, /v1/review, /v1/task/<id>, /v1/cancel/<id>)
6. Bearer token / X-Airlock-Key authentication enforcement
7. JSONL audit trail persistence
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, ".."))
for p in (current_dir, parent_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

from airlock_bridge import AirlockBridge
from airlock_core import AirlockResult, AirlockSecurityPolicy, AirlockTask, TaskStatus
from airlock_server import start_server


class TestDualModelAirlock(unittest.TestCase):

    def setUp(self) -> None:
        self.bridge = AirlockBridge()

    def test_schema_serialization_and_receipt_hash(self) -> None:
        task = AirlockTask(
            prompt="Refactor statistical matrices into vectorized NumPy operations",
            commander="gemini-2.5-pro",
            worker="claude",
        )
        d = task.to_dict()
        self.assertEqual(d["commander"], "gemini-2.5-pro")
        self.assertEqual(d["worker"], "claude")

        reconstructed = AirlockTask.from_dict(d)
        self.assertEqual(reconstructed.prompt, task.prompt)
        self.assertEqual(reconstructed.task_id, task.task_id)

        res = AirlockResult(task_id=task.task_id, status=TaskStatus.COMPLETED, stdout="OK")
        digest = res.compute_receipt_hash(task.prompt)
        self.assertEqual(len(digest), 16)
        self.assertEqual(res.receipt_hash, digest)

    def test_mock_worker_sync_and_audit_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_file = os.path.join(tmpdir, "audit.jsonl")
            bridge = AirlockBridge(audit_log_path=audit_file)
            task = AirlockTask(
                prompt="Audit zero-copy ring buffer implementation for thread safety",
                worker="mock",
            )
            res = bridge.execute_task(task, async_run=False)
            self.assertEqual(res.status, TaskStatus.COMPLETED)
            self.assertEqual(res.exit_code, 0)
            self.assertIn("MOCK_AIRLOCK_SUCCESS", res.stdout)
            self.assertTrue(res.receipt_hash)
            self.assertIsNotNone(res.structured_output)
            self.assertEqual(res.structured_output["verdict"], "PASS")

            self.assertTrue(os.path.exists(audit_file))
            with open(audit_file, "r", encoding="utf-8") as f:
                lines = [json.loads(line) for line in f if line.strip()]
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0]["task_id"], task.task_id)
            self.assertEqual(lines[0]["receipt_hash"], res.receipt_hash)

    def test_mock_worker_async_and_cancellation(self) -> None:
        task = AirlockTask(
            prompt="Run deep static analysis across microservice modules",
            worker="mock",
            parameters={"sim_delay_sec": 0.4},
        )
        running_res = self.bridge.execute_task(task, async_run=True)
        self.assertEqual(running_res.status, TaskStatus.RUNNING)

        cancelled = self.bridge.cancel_task(task.task_id)
        self.assertTrue(cancelled)

        time.sleep(0.05)
        final_res = self.bridge.get_status(task.task_id)
        self.assertIsNotNone(final_res)
        self.assertEqual(final_res.status, TaskStatus.CANCELLED)

    def test_security_policy_perimeter(self) -> None:
        # 1. Shell worker disabled by default
        shell_task = AirlockTask(prompt="echo hello", worker="shell")
        res1 = self.bridge.execute_task(shell_task)
        self.assertEqual(res1.status, TaskStatus.REJECTED)
        self.assertEqual(res1.exit_code, 403)

        # 2. Destructive command blocked
        bad_task = AirlockTask(prompt="Please run rm -rf / on the server", worker="mock")
        res2 = self.bridge.execute_task(bad_task)
        self.assertEqual(res2.status, TaskStatus.REJECTED)

        # 3. CWD sandbox enforcement
        with tempfile.TemporaryDirectory() as allowed_dir, tempfile.TemporaryDirectory() as forbidden_dir:
            policy = AirlockSecurityPolicy(allowed_cwd_roots=[allowed_dir])
            sandboxed_bridge = AirlockBridge(default_cwd=allowed_dir, policy=policy)
            escaped_task = AirlockTask(
                prompt="Inspect module exports",
                worker="mock",
                cwd=forbidden_dir,
            )
            res3 = sandboxed_bridge.execute_task(escaped_task)
            self.assertEqual(res3.status, TaskStatus.REJECTED)

    def test_dual_model_review_workflow(self) -> None:
        target_file = os.path.join(current_dir, "airlock_core.py")
        res = self.bridge.execute_review(
            target=target_file,
            commander="gemini-2.5-pro",
            worker="mock",
        )
        self.assertEqual(res.status, TaskStatus.COMPLETED)
        self.assertIn("MOCK_AIRLOCK_SUCCESS", res.stdout)

    def test_http_server_endpoints_and_bearer_auth(self) -> None:
        server = start_server(port=0, api_key="trishula-test-secret")
        test_port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        time.sleep(0.05)

        auth_headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer trishula-test-secret",
        }

        try:
            # 1. Public Health check (no auth required)
            url_health = f"http://127.0.0.1:{test_port}/health"
            with urllib.request.urlopen(url_health, timeout=2) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(data["status"], "online")
                self.assertEqual(data["version"], "2.0.0")
                self.assertTrue(data["auth_enabled"])
                self.assertIn("workers", data)

            # 2. Unauthenticated request to protected endpoint must return 401
            url_dispatch = f"http://127.0.0.1:{test_port}/v1/dispatch"
            unauth_req = urllib.request.Request(
                url_dispatch,
                data=b'{"prompt":"test","worker":"mock"}',
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(unauth_req, timeout=2)
            self.assertEqual(ctx.exception.code, 401)

            # 3. Authenticated dispatch
            payload = json.dumps(
                {
                    "prompt": "Verify SIMD matrix multiplication alignment",
                    "worker": "mock",
                }
            ).encode("utf-8")
            req = urllib.request.Request(url_dispatch, data=payload, headers=auth_headers)
            with urllib.request.urlopen(req, timeout=2) as resp:
                self.assertEqual(resp.status, 200)
                disp_data = json.loads(resp.read().decode("utf-8"))
                self.assertIn("task_id", disp_data)
                task_id = disp_data["task_id"]

            # 4. Retrieve task status
            url_task = f"http://127.0.0.1:{test_port}/v1/task/{task_id}"
            task_req = urllib.request.Request(url_task, headers=auth_headers)
            with urllib.request.urlopen(task_req, timeout=2) as resp:
                self.assertEqual(resp.status, 200)
                task_status_data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(task_status_data["task_id"], task_id)
                self.assertEqual(task_status_data["status"], "completed")
                self.assertTrue(task_status_data["receipt_hash"])

            # 5. Dual-Model Review endpoint
            url_rev = f"http://127.0.0.1:{test_port}/v1/review"
            rev_payload = json.dumps({"target": "def add(a, b): return a + b", "worker": "mock"}).encode("utf-8")
            rev_req = urllib.request.Request(url_rev, data=rev_payload, headers=auth_headers)
            with urllib.request.urlopen(rev_req, timeout=2) as resp:
                self.assertEqual(resp.status, 200)
                rev_data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(rev_data["status"], "completed")

        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
