"""
Dual-Model-Airlock
Ultra-lightweight IPC and REST bridge between Google Gemini and Claude Code CLI.
Re-engineered, framework-free alternative to AutoGen 0.4.
"""

from .airlock_core import AirlockTask, AirlockResult, TaskStatus, BridgeSession
from .airlock_bridge import AirlockBridge

__version__ = "1.0.0"
__all__ = ["AirlockTask", "AirlockResult", "TaskStatus", "BridgeSession", "AirlockBridge"]
