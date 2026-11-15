"""
Dual-Model-Airlock (v2.0.0)
Ultra-lightweight IPC and REST bridge between Google Gemini, Claude Code CLI, Codex, and Aider.
Re-engineered, framework-free alternative to AutoGen 0.4.
https://trishulasoftware.github.io
"""

try:
    from .airlock_bridge import AirlockBridge
    from .airlock_core import AirlockResult, AirlockSecurityPolicy, AirlockTask, BridgeSession, TaskStatus
except ImportError:
    from airlock_bridge import AirlockBridge
    from airlock_core import AirlockResult, AirlockSecurityPolicy, AirlockTask, BridgeSession, TaskStatus

__version__ = "2.0.0"
__all__ = [
    "AirlockTask",
    "AirlockResult",
    "AirlockSecurityPolicy",
    "TaskStatus",
    "BridgeSession",
    "AirlockBridge",
]
