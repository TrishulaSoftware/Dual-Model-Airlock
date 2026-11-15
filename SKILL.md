---
name: dual-model-airlock
description: Dispatch coding, refactoring, and adversarial code-review tasks between Google Gemini, Claude Code CLI, Codex, and Aider through a zero-dependency IPC and REST security airlock. Use when orchestrating multi-model code generation, cross-model verification, or background worker delegation.
version: 2.0.0
author: Trishula Software (https://trishulasoftware.github.io)
license: MIT
---

# Dual-Model-Airlock Skill (`SKILL.md`)

Use **Dual-Model-Airlock** whenever an agent needs a second frontier model (such as **Claude Code CLI** `claude -p`, **Gemini CLI**, **Codex**, or **Aider**) to execute a scoped coding task, run an independent security/bug review, or process work asynchronously behind a deterministic security perimeter.

## Core Capabilities
1. **Commander ↔ Worker Delegation:** Dispatch implementation prompts from your primary agent to a secondary CLI coding agent (`claude`, `gemini`, `codex`, `aider`) and receive structured output, exit codes, modified file artifacts, and a SHA-256 execution receipt (`receipt_hash`).
2. **Adversarial Dual-Model Code Review (`review`):** Send a target file or diff crafted by Model A directly to Model B for a structured 4-point audit (Bugs/Race Conditions, Security Vulnerabilities, Performance, and PASS/FAIL Verdict).
3. **Perimeter Security Gate (`AirlockSecurityPolicy`):** Blocks destructive command patterns, enforces workspace `allowed_cwd_roots` sandboxing, gates raw shell execution, and supports Bearer token authentication (`AIRLOCK_API_KEY`).

---

## Workflow 1: Check Available Local Coding Agents

Before dispatching a task, discover which CLI coding agents are installed on the host machine:

```bash
python cli.py workers
```

Example output:
```json
{
  "claude": { "available": true, "binary": "/usr/local/bin/claude" },
  "gemini": { "available": true, "binary": "/usr/local/bin/gemini" },
  "codex":  { "available": false, "binary": null },
  "aider":  { "available": false, "binary": null },
  "mock":   { "available": true, "binary": "builtin://mock" }
}
```

---

## Workflow 2: Direct Cross-Model Task Dispatch (IPC)

To delegate a coding or refactoring task directly to Claude Code CLI (or Gemini CLI):

```bash
python cli.py dispatch "Refactor vector_engine.py to eliminate redundant allocations and add type hints" --worker claude --timeout 180
```

Or via Python inside an orchestration script:

```python
from airlock_core import AirlockTask, AirlockSecurityPolicy
from airlock_bridge import AirlockBridge

policy = AirlockSecurityPolicy(allowed_cwd_roots=["."])
bridge = AirlockBridge(policy=policy, audit_log_path="airlock_audit.jsonl")

task = AirlockTask(
    prompt="Write unit tests for auth_middleware.py covering expired JWTs",
    commander="gemini-2.5-pro",
    worker="claude",
    timeout_sec=180,
)
result = bridge.execute_task(task)
print(result.status.value, result.receipt_hash, result.artifacts_generated)
print(result.stdout)
```

---

## Workflow 3: Dual-Model Adversarial Code Review

After writing or modifying a critical file with your primary model, invoke the secondary model as an independent auditor:

```bash
python cli.py review src/payment_service.py --worker claude --commander gemini-2.5-pro
```

---

## Workflow 4: Threaded Local REST Gateway (`127.0.0.1:9090`)

For persistent multi-agent pipelines, start the threaded REST daemon:

```bash
python cli.py serve --port 9090 --api-key "$AIRLOCK_API_KEY" --audit-log airlock_audit.jsonl
```

### REST Endpoints
- `GET /health` — Server status, version, and detected worker binaries.
- `GET /v1/workers` — Installed CLI worker inventory.
- `POST /v1/dispatch` — Dispatch synchronous or asynchronous (`"async": true`) worker tasks.
- `POST /v1/review` — Trigger a structured cross-model file/snippet code audit.
- `GET /v1/task/<task_id>` — Poll task status, output, modified files, and `receipt_hash`.
- `POST /v1/cancel/<task_id>` — Deterministically terminate a running worker subprocess.
