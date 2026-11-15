"""
Dual-Model-Airlock Bridge Engine (v2.0.0)
Manages cross-model execution, subprocess pipes, real OS process cancellation,
structured output extraction, and dual-model adversarial code review.
Part of the Trishula Sovereign Architecture — https://trishulasoftware.github.io
"""

import json
import os
import shlex
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    from .airlock_core import AirlockResult, AirlockSecurityPolicy, AirlockTask, TaskStatus
except ImportError:
    from airlock_core import AirlockResult, AirlockSecurityPolicy, AirlockTask, TaskStatus


class AirlockBridge:
    """
    Subprocess & IPC bridge orchestrator.
    Executes worker tasks dispatched by a Commander agent, tracks live OS processes
    for deterministic cancellation, enforces AirlockSecurityPolicy, and records audit receipts.
    """

    SUPPORTED_CLI_WORKERS = ("claude", "gemini", "codex", "aider")

    def __init__(
        self,
        default_cwd: Optional[str] = None,
        policy: Optional[AirlockSecurityPolicy] = None,
        audit_log_path: Optional[str] = None,
    ):
        self.default_cwd = os.path.abspath(default_cwd or os.getcwd())
        self.policy = policy or AirlockSecurityPolicy()
        self.audit_log_path = audit_log_path
        self.tasks: Dict[str, AirlockTask] = {}
        self.results: Dict[str, AirlockResult] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._processes: Dict[str, subprocess.Popen] = {}
        self._cancelled_ids: set[str] = set()
        self._lock = threading.Lock()

    def _resolve_worker_binary(self, worker_name: str = "claude") -> Optional[str]:
        """Resolves the executable path for a target CLI agent across Windows, macOS, and Linux."""
        base = worker_name.lower().strip()
        if base.startswith("claude"):
            base = "claude"
        elif base.startswith("gemini"):
            base = "gemini"
        elif base.startswith("codex"):
            base = "codex"
        elif base.startswith("aider"):
            base = "aider"

        candidates = [f"{base}.cmd", f"{base}.exe", base] if os.name == "nt" else [base]
        for cand in candidates:
            found = shutil.which(cand)
            if found:
                return found

        if os.name == "nt":
            npm_dir = os.path.expandvars(r"%APPDATA%\npm")
            for ext in (".cmd", ".exe", ".ps1"):
                npm_cand = os.path.join(npm_dir, f"{base}{ext}")
                if os.path.exists(npm_cand):
                    return npm_cand

        return None

    def _resolve_claude_binary(self) -> Optional[str]:
        """Backwards-compatible helper resolving the Claude Code CLI binary."""
        return self._resolve_worker_binary("claude")

    def discover_workers(self) -> Dict[str, Dict[str, Any]]:
        """Probes local environment for installed AI coding CLI workers."""
        inventory: Dict[str, Dict[str, Any]] = {}
        for w in self.SUPPORTED_CLI_WORKERS:
            binary = self._resolve_worker_binary(w)
            inventory[w] = {
                "available": bool(binary),
                "binary": binary,
            }
        inventory["mock"] = {"available": True, "binary": "builtin://mock"}
        return inventory

    def _snapshot_git_Dirty_files(self, cwd: str) -> set[str]:
        """Returns modified/untracked file paths in cwd if inside a git worktree."""
        try:
            proc = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=3,
                shell=False,
            )
            if proc.returncode != 0:
                return set()
            files = set()
            for line in (proc.stdout or "").splitlines():
                if len(line) > 3:
                    files.add(line[3:].strip())
            return files
        except Exception:
            return set()

    def _append_audit_log(self, task: AirlockTask, result: AirlockResult) -> None:
        if not self.audit_log_path:
            return
        try:
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "task_id": task.task_id,
                "commander": task.commander,
                "worker": task.worker,
                "status": result.status.value,
                "exit_code": result.exit_code,
                "duration_sec": result.duration_sec,
                "receipt_hash": result.receipt_hash,
                "artifacts_generated": result.artifacts_generated,
            }
            with open(self.audit_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass

    def execute_task(self, task: AirlockTask, async_run: bool = False) -> AirlockResult:
        """Validates and dispatches a task synchronously or asynchronously."""
        valid, reason = self.policy.validate_task(task, self.default_cwd)
        if not valid:
            rejected = AirlockResult(
                task_id=task.task_id,
                status=TaskStatus.REJECTED,
                stdout="",
                stderr=f"[AIRLOCK_SECURITY_GATE] {reason}",
                exit_code=403,
                duration_sec=0.0,
                metadata={"policy_violation": reason},
            )
            rejected.compute_receipt_hash(task.prompt)
            with self._lock:
                self.tasks[task.task_id] = task
                self.results[task.task_id] = rejected
            self._append_audit_log(task, rejected)
            return rejected

        with self._lock:
            self.tasks[task.task_id] = task
            running_res = AirlockResult(
                task_id=task.task_id,
                status=TaskStatus.RUNNING,
            )
            self.results[task.task_id] = running_res

        if async_run:
            t = threading.Thread(target=self._run_worker, args=(task,), daemon=True)
            with self._lock:
                self._threads[task.task_id] = t
            t.start()
            return running_res
        else:
            return self._run_worker(task)

    def _build_cli_command(self, worker_type: str, binary: str, task: AirlockTask) -> List[str]:
        """Constructs a shell-safe argument vector for the target CLI agent."""
        output_fmt = task.parameters.get("output_format")
        if "claude" in worker_type:
            cmd = [binary, "-p", str(task.prompt)]
            if output_fmt in ("json", "text", "stream-json"):
                cmd.extend(["--output-format", output_fmt])
            if task.parameters.get("dangerously_skip_permissions") and self.policy.allow_skip_permissions:
                cmd.append("--dangerously-skip-permissions")
        elif "gemini" in worker_type:
            cmd = [binary, "-p", str(task.prompt)]
            if output_fmt == "json":
                cmd.extend(["--output-format", "json"])
        elif "codex" in worker_type:
            cmd = [binary, "-q", str(task.prompt)]
        elif "aider" in worker_type:
            cmd = [binary, "--message", str(task.prompt), "--yes"]
        else:
            cmd = [binary, str(task.prompt)]

        if os.name == "nt" and binary.lower().endswith((".cmd", ".bat")):
            return ["cmd.exe", "/d", "/s", "/c", subprocess.list2cmdline(cmd)]
        return cmd

    def _run_worker(self, task: AirlockTask) -> AirlockResult:
        start_time = time.time()
        working_dir = os.path.abspath(task.cwd or self.default_cwd)
        worker_type = task.worker.lower().strip()

        # 1. Mock / Simulation Worker
        if worker_type in ("mock", "test", "sim"):
            sim_delay = float(task.parameters.get("sim_delay_sec", 0.05))
            steps = max(1, int(sim_delay / 0.01))
            for _ in range(steps):
                with self._lock:
                    if task.task_id in self._cancelled_ids:
                        duration = time.time() - start_time
                        res = AirlockResult(
                            task_id=task.task_id,
                            status=TaskStatus.CANCELLED,
                            stdout="",
                            stderr="Task cancelled by operator.",
                            exit_code=-15,
                            duration_sec=round(duration, 3),
                        )
                        res.compute_receipt_hash(task.prompt)
                        self.results[task.task_id] = res
                        return res
                time.sleep(min(0.01, sim_delay))

            duration = max(0.001, time.time() - start_time)
            mock_payload = {
                "verdict": "PASS",
                "worker": "mock",
                "commander": task.commander,
                "summary": f"[MOCK_AIRLOCK_SUCCESS] Worker processed prompt: '{task.prompt[:80]}'",
            }
            res = AirlockResult(
                task_id=task.task_id,
                status=TaskStatus.COMPLETED,
                stdout=mock_payload["summary"],
                stderr="",
                exit_code=0,
                duration_sec=round(duration, 3),
                structured_output=mock_payload,
                metadata={"worker": "mock", "prompt_len": len(task.prompt)},
            )
            res.compute_receipt_hash(task.prompt)
            with self._lock:
                self.results[task.task_id] = res
            self._append_audit_log(task, res)
            return res

        # 2. CLI Agent Workers (Claude Code, Gemini CLI, Codex, Aider)
        matched_cli = next((w for w in self.SUPPORTED_CLI_WORKERS if w in worker_type), None)
        if matched_cli:
            binary = self._resolve_worker_binary(matched_cli)
            if not binary:
                duration = time.time() - start_time
                res = AirlockResult(
                    task_id=task.task_id,
                    status=TaskStatus.FAILED,
                    stdout="",
                    stderr=f"{matched_cli.capitalize()} CLI binary not found on PATH or %APPDATA%\\npm",
                    exit_code=127,
                    duration_sec=round(duration, 3),
                )
                res.compute_receipt_hash(task.prompt)
                with self._lock:
                    self.results[task.task_id] = res
                self._append_audit_log(task, res)
                return res

            before_files = self._snapshot_git_Dirty_files(working_dir)
            cmd = self._build_cli_command(worker_type, binary, task)
            return self._execute_popen(task, cmd, working_dir, start_time, before_files, {"binary": binary, "worker": matched_cli})

        # 3. Policy-Gated Raw Command / Python Worker
        if worker_type in ("python", "cmd", "shell"):
            if isinstance(task.prompt, list):
                cmd = [str(x) for x in task.prompt]
            else:
                cmd = shlex.split(str(task.prompt), posix=(os.name != "nt"))
            before_files = self._snapshot_git_Dirty_files(working_dir)
            return self._execute_popen(task, cmd, working_dir, start_time, before_files, {"worker": worker_type})

        # 4. Unknown worker type
        duration = time.time() - start_time
        res = AirlockResult(
            task_id=task.task_id,
            status=TaskStatus.FAILED,
            stdout="",
            stderr=f"Unsupported worker type: {worker_type}",
            exit_code=1,
            duration_sec=round(duration, 3),
        )
        res.compute_receipt_hash(task.prompt)
        with self._lock:
            self.results[task.task_id] = res
        self._append_audit_log(task, res)
        return res

    def _execute_popen(
        self,
        task: AirlockTask,
        cmd: List[str],
        working_dir: str,
        start_time: float,
        before_files: set[str],
        meta: Dict[str, Any],
    ) -> AirlockResult:
        proc: Optional[subprocess.Popen] = None
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=working_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
            )
            with self._lock:
                self._processes[task.task_id] = proc
                if task.task_id in self._cancelled_ids:
                    proc.kill()

            stdout, stderr = proc.communicate(timeout=task.timeout_sec)
            duration = max(0.001, time.time() - start_time)

            with self._lock:
                was_cancelled = task.task_id in self._cancelled_ids
                self._processes.pop(task.task_id, None)

            if was_cancelled:
                status = TaskStatus.CANCELLED
                exit_code = -15
            else:
                exit_code = proc.returncode if proc.returncode is not None else 1
                status = TaskStatus.COMPLETED if exit_code == 0 else TaskStatus.FAILED

            after_files = self._snapshot_git_Dirty_files(working_dir)
            new_artifacts = sorted(after_files - before_files)

            structured = None
            stripped = (stdout or "").strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                try:
                    structured = json.loads(stripped)
                except Exception:
                    structured = None

            res = AirlockResult(
                task_id=task.task_id,
                status=status,
                stdout=stdout or "",
                stderr=stderr or "",
                exit_code=exit_code,
                duration_sec=round(duration, 3),
                artifacts_generated=new_artifacts,
                structured_output=structured,
                metadata=meta,
            )
        except subprocess.TimeoutExpired:
            if proc is not None:
                try:
                    proc.kill()
                    proc.communicate(timeout=2)
                except Exception:
                    pass
            with self._lock:
                self._processes.pop(task.task_id, None)
            duration = max(0.001, time.time() - start_time)
            res = AirlockResult(
                task_id=task.task_id,
                status=TaskStatus.TIMED_OUT,
                stdout="",
                stderr=f"Task execution timed out after {task.timeout_sec}s",
                exit_code=-1,
                duration_sec=round(duration, 3),
                metadata=meta,
            )
        except Exception as e:
            with self._lock:
                self._processes.pop(task.task_id, None)
            duration = max(0.001, time.time() - start_time)
            res = AirlockResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                stdout="",
                stderr=f"Subprocess execution error: {str(e)}",
                exit_code=1,
                duration_sec=round(duration, 3),
                metadata=meta,
            )

        res.compute_receipt_hash(task.prompt)
        with self._lock:
            self.results[task.task_id] = res
        self._append_audit_log(task, res)
        return res

    def execute_review(
        self,
        target: str,
        commander: str = "gemini-2.5-pro",
        worker: str = "claude",
        cwd: Optional[str] = None,
        timeout_sec: int = 180,
    ) -> AirlockResult:
        """
        Dual-Model Cross-Verification workflow:
        Packages a file path, git diff, or code snippet into a structured adversarial review prompt
        and dispatches it to the secondary model worker.
        """
        work_dir = os.path.abspath(cwd or self.default_cwd)
        candidate_file = os.path.join(work_dir, target) if not os.path.isabs(target) else target

        if os.path.isfile(candidate_file):
            try:
                with open(candidate_file, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(50_000)
                context_block = f"FILE: {target}\n```\n{content}\n```"
            except Exception as e:
                context_block = f"TARGET: {target} (read error: {e})"
        else:
            context_block = f"TARGET SPECIFICATION / SNIPPET:\n{target}"

        review_prompt = (
            "You are acting as the Secondary Verification Model inside Dual-Model-Airlock.\n"
            f"Originating Commander Model: {commander}\n"
            "Perform a rigorous adversarial code audit covering:\n"
            "1. Bugs, race conditions, or edge-case failures\n"
            "2. Security vulnerabilities (injection, path traversal, auth bypass)\n"
            "3. Performance bottlenecks and zero-dependency compliance\n"
            "4. Final Verdict: PASS or FAIL with concrete remediation steps.\n\n"
            f"{context_block}"
        )
        task = AirlockTask(
            prompt=review_prompt,
            commander=commander,
            worker=worker,
            cwd=work_dir,
            timeout_sec=timeout_sec,
            parameters={"workflow": "dual_model_review", "target": target},
        )
        return self.execute_task(task, async_run=False)

    def get_status(self, task_id: str) -> Optional[AirlockResult]:
        """Returns the result or status of a previously dispatched task."""
        with self._lock:
            return self.results.get(task_id)

    def cancel_task(self, task_id: str) -> bool:
        """Terminates a running task's underlying OS subprocess and marks it CANCELLED."""
        with self._lock:
            res = self.results.get(task_id)
            if not res or res.status not in (TaskStatus.RUNNING, TaskStatus.QUEUED):
                return False
            self._cancelled_ids.add(task_id)
            proc = self._processes.pop(task_id, None)
            res.status = TaskStatus.CANCELLED
            res.stderr = (res.stderr + "\nTask cancelled by operator.").strip()
            res.exit_code = -15
            res.compute_receipt_hash(self.tasks[task_id].prompt if task_id in self.tasks else "")

        if proc is not None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception:
                pass
        return True
