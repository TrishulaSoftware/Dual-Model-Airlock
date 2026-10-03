"""
Dual-Model-Airlock Protocol Core
Canonical schemas, message validation, and session models.
"""

import json
import uuid
from enum import Enum
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


@dataclass
class AirlockTask:
    task_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    prompt: str = ""
    commander: str = "gemini"        # e.g. "gemini-2.5-pro", "gemini-flash"
    worker: str = "claude"           # e.g. "claude-code-cli", "claude-3-7-sonnet"
    cwd: Optional[str] = None
    timeout_sec: int = 300
    parameters: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AirlockTask":
        return cls(
            task_id=data.get("task_id") or str(uuid.uuid4())[:8],
            prompt=data.get("prompt", ""),
            commander=data.get("commander", "gemini"),
            worker=data.get("worker", "claude"),
            cwd=data.get("cwd"),
            timeout_sec=data.get("timeout_sec", 300),
            parameters=data.get("parameters", {}),
            created_at=data.get("created_at") or datetime.now(timezone.utc).isoformat()
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
    metadata: Dict[str, Any] = field(default_factory=dict)
    completed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
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
