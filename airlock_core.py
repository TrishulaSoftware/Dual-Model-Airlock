"""
Dual-Model-Airlock Protocol Core (v2.0.0)
Canonical schemas, security policy gates, cryptographic audit receipts, and session models.
Part of the Trishula Sovereign Architecture — https://trishulasoftware.github.io
"""

import hashlib
import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


# Destructive shell/command patterns neutralized at the Airlock perimeter
_DESTRUCTIVE_PATTERNS = [
    re.compile(r"\brm\s+-[rf]{1,2}\s+/(?:\s|$|\*)", re.IGNORECASE),
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r"\bformat\s+[a-z]:", re.IGNORECASE),
    re.compile(r"\bdel\s+/[fqs].*\b[a-z]:\\", re.IGNORECASE),
    re.compile(r":\(\)\s*\{\s*:\|:\s*&\s*\}\s*;", re.IGNORECASE),
]


@dataclass
class AirlockSecurityPolicy:
    """
    Perimeter security controls neutralizing command injection, directory traversal,
    and unauthorized raw-shell execution.
    """
    allow_shell_worker: bool = False
    allow_skip_permissions: bool = False
    allowed_cwd_roots: List[str] = field(default_factory=list)
    max_prompt_chars: int = 200_000
    block_destructive_patterns: bool = True

    def validate_task(self, task: "AirlockTask", default_cwd: str) -> Tuple[bool, str]:
        if not task.prompt or not str(task.prompt).strip():
            return False, "Task prompt cannot be empty."

        if len(str(task.prompt)) > self.max_prompt_chars:
            return False, f"Prompt length ({len(str(task.prompt))}) exceeds max_prompt_chars ({self.max_prompt_chars})."

        worker_type = task.worker.lower().strip()
        if worker_type in ("python", "cmd", "shell") and not self.allow_shell_worker:
            return (
                False,
                f"Worker '{worker_type}' is disabled by AirlockSecurityPolicy (set allow_shell_worker=True to enable).",
            )

        if task.parameters.get("dangerously_skip_permissions") and not self.allow_skip_permissions:
            return (
                False,
                "Parameter 'dangerously_skip_permissions' is blocked by AirlockSecurityPolicy.",
            )

        if self.block_destructive_patterns:
            prompt_str = str(task.prompt)
            for pat in _DESTRUCTIVE_PATTERNS:
                if pat.search(prompt_str):
                    return False, f"Destructive command pattern blocked by Airlock perimeter: {pat.pattern}"

        target_cwd = os.path.abspath(task.cwd or default_cwd)
        if not os.path.isdir(target_cwd):
            return False, f"Working directory does not exist: {target_cwd}"

        if self.allowed_cwd_roots:
            resolved_target = Path(target_cwd).resolve()
            allowed = False
            for root in self.allowed_cwd_roots:
                try:
                    resolved_root = Path(root).resolve()
                    if resolved_target == resolved_root or resolved_root in resolved_target.parents:
                        allowed = True
                        break
                except Exception:
                    continue
            if not allowed:
                return False, f"Working directory '{target_cwd}' is outside allowed_cwd_roots."

        return True, "OK"


@dataclass
class AirlockTask:
    task_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    prompt: str = ""
    commander: str = "gemini"        # e.g. "gemini-2.5-pro", "gemini-flash"
    worker: str = "claude"           # e.g. "claude", "gemini", "codex", "mock"
    cwd: Optional[str] = None
    timeout_sec: int = 300
    parameters: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AirlockTask":
        timeout_val = data.get("timeout_sec", 300)
        try:
            timeout_val = max(1, int(timeout_val))
        except (ValueError, TypeError):
            timeout_val = 300

        return cls(
            task_id=data.get("task_id") or str(uuid.uuid4())[:8],
            prompt=data.get("prompt", ""),
            commander=data.get("commander", "gemini"),
            worker=data.get("worker", "claude"),
            cwd=data.get("cwd"),
            timeout_sec=timeout_val,
            parameters=data.get("parameters") or {},
            created_at=data.get("created_at") or datetime.now(timezone.utc).isoformat(),
        )


@dataclass
class AirlockResult:
    task_id: str
    status: TaskStatus
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    duration_sec: float = 0.0
    artifacts_generated: List[str] = field(default_factory=list)
    structured_output: Optional[Dict[str, Any]] = None
    receipt_hash: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    completed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def compute_receipt_hash(self, prompt: str = "") -> str:
        payload = f"{self.task_id}|{self.status.value}|{self.exit_code}|{prompt}|{self.stdout}|{self.stderr}"
        self.receipt_hash = hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()[:16]
        return self.receipt_hash

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value if isinstance(self.status, TaskStatus) else str(self.status)
        return d


@dataclass
class BridgeSession:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    commander_model: str = "gemini-2.5-pro"
    worker_client: str = "claude-code"
    active_tasks: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_ping: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
