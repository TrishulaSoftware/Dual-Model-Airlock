"""
Dual-Model-Airlock Bridge Engine
Manages execution, subprocess pipes, and handoffs between Gemini commander and Claude Code worker.
"""

import os
import sys
import time
import shutil
import subprocess
import threading
from typing import Dict, Any, Optional, Callable
from datetime import datetime, timezone

from .airlock_core import AirlockTask, AirlockResult, TaskStatus


class AirlockBridge:
    """
    Subprocess & API bridge orchestrator.
    Executes worker tasks dispatched by Gemini commander and captures output, exit codes, and artifacts.
    """

    def __init__(self, default_cwd: Optional[str] = None):
        self.default_cwd = default_cwd or os.getcwd()
        self.tasks: Dict[str, AirlockTask] = {}
        self.results: Dict[str, AirlockResult] = {}
        self._threads: Dict[str, threading.Thread] = {}

    def _resolve_claude_binary(self) -> Optional[str]:
        """Resolves the executable path for Claude Code CLI on Windows/Linux."""
        # Check standard binary name
        cmd = shutil.which("claude.cmd") or shutil.which("claude.exe") or shutil.which("claude")
        if cmd:
            return cmd
        
        # Check npm global directory on Windows
        npm_path = os.path.expandvars(r"%APPDATA%\npm\claude.cmd")
        if os.path.exists(npm_path):
            return npm_path

        return None

    def execute_task(self, task: AirlockTask, async_run: bool = False) -> AirlockResult:
        """Dispatches and runs a task synchronously or asynchronously."""
        self.tasks[task.task_id] = task
        self.results[task.task_id] = AirlockResult(
            task_id=task.task_id,
            status=TaskStatus.RUNNING
        )

        if async_run:
            t = threading.Thread(target=self._run_worker, args=(task,), daemon=True)
            self._threads[task.task_id] = t
            t.start()
            return self.results[task.task_id]
        else:
            return self._run_worker(task)

    def _run_worker(self, task: AirlockTask) -> AirlockResult:
        start_time = time.time()
        working_dir = task.cwd or self.default_cwd
        worker_type = task.worker.lower()

        # Handle Mock Worker (for testing & simulation)
        if worker_type in ("mock", "test", "sim"):
            time.sleep(0.05)
            duration = time.time() - start_time
            res = AirlockResult(
                task_id=task.task_id,
                status=TaskStatus.COMPLETED,
                stdout=f"[MOCK_AIRLOCK_SUCCESS] Worker processed prompt: '{task.prompt[:60]}...'",
                stderr="",
                exit_code=0,
                duration_sec=round(duration, 3),
                metadata={"worker": "mock", "prompt_len": len(task.prompt)}
            )
            self.results[task.task_id] = res
            return res

        # Handle Claude Code CLI Worker
        if "claude" in worker_type:
            claude_bin = self._resolve_claude_binary()
            if not claude_bin:
                duration = time.time() - start_time
                res = AirlockResult(
                    task_id=task.task_id,
                    status=TaskStatus.FAILED,
                    stdout="",
                    stderr="Claude CLI binary not found on PATH or %APPDATA%\\npm\\claude.cmd",
                    exit_code=127,
                    duration_sec=round(duration, 3)
                )
                self.results[task.task_id] = res
                return res

            # Construct headless print prompt command
            # claude -p "<prompt>" --output-format json/text
            cmd = [claude_bin, "-p", task.prompt]
            if task.parameters.get("dangerously_skip_permissions", False):
                cmd.append("--dangerously-skip-permissions")

            try:
                proc = subprocess.run(
                    cmd,
                    cwd=working_dir,
                    capture_output=True,
                    text=True,
                    timeout=task.timeout_sec,
                    shell=(os.name == "nt")  # Needs shell on Windows for .cmd/.ps1 invocation
                )
                duration = time.time() - start_time
                status = TaskStatus.COMPLETED if proc.returncode == 0 else TaskStatus.FAILED
                res = AirlockResult(
                    task_id=task.task_id,
                    status=status,
                    stdout=proc.stdout or "",
                    stderr=proc.stderr or "",
                    exit_code=proc.returncode,
                    duration_sec=round(duration, 3),
                    metadata={"binary": claude_bin, "command": "claude -p"}
                )
            except subprocess.TimeoutExpired:
                duration = time.time() - start_time
                res = AirlockResult(
                    task_id=task.task_id,
                    status=TaskStatus.TIMED_OUT,
                    stdout="",
                    stderr=f"Task execution timed out after {task.timeout_sec}s",
                    exit_code=-1,
                    duration_sec=round(duration, 3)
                )
            except Exception as e:
                duration = time.time() - start_time
                res = AirlockResult(
                    task_id=task.task_id,
                    status=TaskStatus.FAILED,
                    stdout="",
                    stderr=f"Subprocess execution error: {str(e)}",
                    exit_code=1,
                    duration_sec=round(duration, 3)
                )

            self.results[task.task_id] = res
            return res

        # Handle Raw Command / Python Worker
        if worker_type in ("python", "cmd", "shell"):
            cmd = task.prompt if isinstance(task.prompt, list) else task.prompt.split()
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=working_dir,
                    capture_output=True,
                    text=True,
                    timeout=task.timeout_sec,
                    shell=True
                )
                duration = time.time() - start_time
                status = TaskStatus.COMPLETED if proc.returncode == 0 else TaskStatus.FAILED
                res = AirlockResult(
                    task_id=task.task_id,
                    status=status,
                    stdout=proc.stdout or "",
                    stderr=proc.stderr or "",
                    exit_code=proc.returncode,
                    duration_sec=round(duration, 3)
                )
            except Exception as e:
                duration = time.time() - start_time
                res = AirlockResult(
                    task_id=task.task_id,
                    status=TaskStatus.FAILED,
                    stdout="",
                    stderr=str(e),
                    exit_code=1,
                    duration_sec=round(duration, 3)
                )

            self.results[task.task_id] = res
            return res

        # Unknown worker type
        duration = time.time() - start_time
        res = AirlockResult(
            task_id=task.task_id,
            status=TaskStatus.FAILED,
            stdout="",
            stderr=f"Unsupported worker type: {worker_type}",
            exit_code=1,
            duration_sec=round(duration, 3)
        )
        self.results[task.task_id] = res
        return res

    def get_status(self, task_id: str) -> Optional[AirlockResult]:
        """Returns the result or status of a previously dispatched task."""
        return self.results.get(task_id)

    def cancel_task(self, task_id: str) -> bool:
        """Cancels a running task if possible."""
        if task_id in self.results and self.results[task_id].status == TaskStatus.RUNNING:
            self.results[task_id].status = TaskStatus.CANCELLED
            return True
        return False
