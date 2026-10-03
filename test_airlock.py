"""
Test Suite for Dual-Model-Airlock
Verifies:
1. Core schemas and serialization
2. Synchronous & asynchronous worker dispatch
3. Subprocess execution handling
4. HTTP REST Server endpoints (/health, /v1/dispatch, /v1/task/<id>)
"""

import os
import sys
import time
import json
import urllib.request
import threading
import unittest

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, ".."))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from dual_model_airlock.airlock_core import AirlockTask, AirlockResult, TaskStatus
from dual_model_airlock.airlock_bridge import AirlockBridge
from dual_model_airlock.airlock_server import start_server


class TestDualModelAirlock(unittest.TestCase):

    def setUp(self):
        self.bridge = AirlockBridge()

    def test_schema_serialization(self):
        task = AirlockTask(
            prompt="Refactor statistical models into vectorized NumPy",
            commander="gemini-2.5-pro",
            worker="claude"
        )
        d = task.to_dict()
        self.assertEqual(d["commander"], "gemini-2.5-pro")
        self.assertEqual(d["worker"], "claude")

        reconstructed = AirlockTask.from_dict(d)
        self.assertEqual(reconstructed.prompt, task.prompt)
        self.assertEqual(reconstructed.task_id, task.task_id)

    def test_mock_worker_sync(self):
        task = AirlockTask(
            prompt="Analyze NFL consensus splits",
            worker="mock"
        )
        res = self.bridge.execute_task(task, async_run=False)
        self.assertEqual(res.status, TaskStatus.COMPLETED)
        self.assertEqual(res.exit_code, 0)
        self.assertIn("MOCK_AIRLOCK_SUCCESS", res.stdout)
        self.assertGreater(res.duration_sec, 0.0)

    def test_mock_worker_async(self):
        task = AirlockTask(
            prompt="Background audit of high-leverage props",
            worker="mock"
        )
        running_res = self.bridge.execute_task(task, async_run=True)
        self.assertIn(running_res.status, (TaskStatus.RUNNING, TaskStatus.COMPLETED))

        # Wait briefly for thread to finish
        time.sleep(0.1)
        final_res = self.bridge.get_status(task.task_id)
        self.assertIsNotNone(final_res)
        self.assertEqual(final_res.status, TaskStatus.COMPLETED)

    def test_http_server_endpoints(self):
        test_port = 9898
        server = start_server(port=test_port)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        time.sleep(0.1)

        try:
            # 1. Health check
            url_health = f"http://127.0.0.1:{test_port}/health"
            with urllib.request.urlopen(url_health, timeout=2) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(data["status"], "online")
                self.assertEqual(data["service"], "Dual-Model-Airlock")

            # 2. Dispatch mock task
            url_dispatch = f"http://127.0.0.1:{test_port}/v1/dispatch"
            payload = json.dumps({
                "prompt": "Evaluate spread differential for Buffalo Bills",
                "worker": "mock"
            }).encode("utf-8")
            req = urllib.request.Request(url_dispatch, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=2) as resp:
                self.assertEqual(resp.status, 200)
                disp_data = json.loads(resp.read().decode("utf-8"))
                self.assertIn("task_id", disp_data)
                task_id = disp_data["task_id"]

            # 3. Retrieve task status
            url_task = f"http://127.0.0.1:{test_port}/v1/task/{task_id}"
            with urllib.request.urlopen(url_task, timeout=2) as resp:
                self.assertEqual(resp.status, 200)
                task_status_data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(task_status_data["task_id"], task_id)
                self.assertEqual(task_status_data["status"], "completed")

        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
