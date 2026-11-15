"""
Dual-Model-Airlock CLI (v2.0.0)
Unified command-line interface for dispatching, cross-reviewing, monitoring, and serving multi-model bridge tasks.
Part of the Trishula Sovereign Architecture — https://trishulasoftware.github.io
"""

import argparse
import json
import os
import urllib.error
import urllib.request
from typing import Dict, Optional

try:
    from .airlock_bridge import AirlockBridge
    from .airlock_core import AirlockTask
    from .airlock_server import start_server
except ImportError:
    from airlock_bridge import AirlockBridge
    from airlock_core import AirlockTask
    from airlock_server import start_server


def _auth_headers(api_key: Optional[str] = None) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = api_key or os.environ.get("AIRLOCK_API_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def check_health(host: str = "127.0.0.1", port: int = 9090) -> bool:
    url = f"http://{host}:{port}/health"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(f"[OK] Airlock Server is ONLINE at {url}")
            print(json.dumps(data, indent=2))
            return True
    except Exception as e:
        print(f"[WARN] Airlock Server not reachable at {url}: {e}")
        bridge = AirlockBridge()
        print(json.dumps({"local_workers": bridge.discover_workers()}, indent=2))
        return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dual-Model-Airlock v2.0.0: Sovereign Gemini ↔ Claude Code CLI Bridge"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. Serve command
    p_serve = subparsers.add_parser("serve", help="Start the threaded Airlock HTTP/REST server")
    p_serve.add_argument("--port", "-p", type=int, default=9090, help="Port to listen on (default: 9090)")
    p_serve.add_argument("--host", type=str, default="127.0.0.1", help="Host interface (default: 127.0.0.1)")
    p_serve.add_argument("--api-key", type=str, default=None, help="Optional Bearer token / API key")
    p_serve.add_argument("--audit-log", type=str, default=None, help="Optional JSONL audit log path")

    # 2. Dispatch command
    p_disp = subparsers.add_parser("dispatch", help="Dispatch a task to a worker model")
    p_disp.add_argument("prompt", type=str, help="Instruction or prompt")
    p_disp.add_argument(
        "--worker",
        "-w",
        type=str,
        default="claude",
        choices=["claude", "gemini", "codex", "aider", "mock", "cmd"],
        help="Target worker agent",
    )
    p_disp.add_argument("--commander", "-c", type=str, default="gemini", help="Originating commander model")
    p_disp.add_argument("--cwd", type=str, default=None, help="Working directory for execution")
    p_disp.add_argument("--async-run", "-a", action="store_true", help="Run asynchronously")
    p_disp.add_argument("--timeout", "-t", type=int, default=120, help="Timeout in seconds")
    p_disp.add_argument("--remote", action="store_true", help="Dispatch via running HTTP server")
    p_disp.add_argument("--port", type=int, default=9090, help="Server port if using --remote")
    p_disp.add_argument("--api-key", type=str, default=None, help="Bearer token if server auth is enabled")

    # 3. Review command (Dual-Model Cross-Verification)
    p_rev = subparsers.add_parser("review", help="Run a Dual-Model adversarial code review on a file or snippet")
    p_rev.add_argument("target", type=str, help="File path or code snippet to audit")
    p_rev.add_argument("--worker", "-w", type=str, default="claude", help="Reviewer worker (claude, gemini, mock)")
    p_rev.add_argument("--commander", "-c", type=str, default="gemini-2.5-pro", help="Commander model")
    p_rev.add_argument("--timeout", "-t", type=int, default=180, help="Timeout in seconds")

    # 4. Workers discovery command
    subparsers.add_parser("workers", help="List detected local CLI coding agents (Claude, Gemini, Codex, Aider)")

    # 5. Status command
    p_stat = subparsers.add_parser("status", help="Check remote task status")
    p_stat.add_argument("task_id", type=str, help="Task ID to inspect")
    p_stat.add_argument("--port", type=int, default=9090, help="Server port")
    p_stat.add_argument("--api-key", type=str, default=None, help="Bearer token if server auth is enabled")

    # 6. Cancel command
    p_can = subparsers.add_parser("cancel", help="Cancel a running remote task")
    p_can.add_argument("task_id", type=str, help="Task ID to cancel")
    p_can.add_argument("--port", type=int, default=9090, help="Server port")
    p_can.add_argument("--api-key", type=str, default=None, help="Bearer token if server auth is enabled")

    # 7. Check command
    p_chk = subparsers.add_parser("check", help="Verify local environment and server health")
    p_chk.add_argument("--port", type=int, default=9090, help="Server port")

    args = parser.parse_args()

    if args.command == "serve":
        server = start_server(
            port=args.port,
            host=args.host,
            api_key=args.api_key,
            audit_log_path=args.audit_log,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n[AIRLOCK] Server stopped.")

    elif args.command == "check":
        check_health(port=args.port)

    elif args.command == "workers":
        bridge = AirlockBridge()
        print(json.dumps(bridge.discover_workers(), indent=2))

    elif args.command == "review":
        bridge = AirlockBridge()
        res = bridge.execute_review(
            target=args.target,
            commander=args.commander,
            worker=args.worker,
            timeout_sec=args.timeout,
        )
        print(
            f"[AIRLOCK REVIEW] Status: {res.status.value} | Exit: {res.exit_code} | "
            f"Duration: {res.duration_sec}s | Receipt: {res.receipt_hash}"
        )
        if res.stdout:
            print("\n" + res.stdout)
        if res.stderr:
            print("\nSTDERR:\n" + res.stderr)

    elif args.command == "dispatch":
        if args.remote:
            url = f"http://127.0.0.1:{args.port}/v1/dispatch"
            payload = json.dumps(
                {
                    "prompt": args.prompt,
                    "worker": args.worker,
                    "commander": args.commander,
                    "cwd": args.cwd,
                    "timeout_sec": args.timeout,
                    "async": args.async_run,
                }
            ).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers=_auth_headers(args.api_key))
            try:
                with urllib.request.urlopen(req) as resp:
                    print(resp.read().decode("utf-8"))
            except Exception as e:
                print(f"[ERROR] Failed to dispatch to remote airlock: {e}")
        else:
            bridge = AirlockBridge()
            task = AirlockTask(
                prompt=args.prompt,
                worker=args.worker,
                commander=args.commander,
                cwd=args.cwd,
                timeout_sec=args.timeout,
            )
            print(f"[AIRLOCK] Executing task {task.task_id} with worker '{args.worker}'...")
            res = bridge.execute_task(task, async_run=args.async_run)
            print(
                f"Status: {res.status.value} (exit code: {res.exit_code}, "
                f"{res.duration_sec}s, receipt: {res.receipt_hash})"
            )
            if res.stdout:
                print("\nSTDOUT:\n" + res.stdout)
            if res.stderr:
                print("\nSTDERR:\n" + res.stderr)

    elif args.command == "status":
        url = f"http://127.0.0.1:{args.port}/v1/task/{args.task_id}"
        try:
            req = urllib.request.Request(url, headers=_auth_headers(args.api_key))
            with urllib.request.urlopen(req) as resp:
                print(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"[ERROR] Unable to fetch task status: {e}")

    elif args.command == "cancel":
        url = f"http://127.0.0.1:{args.port}/v1/cancel/{args.task_id}"
        try:
            req = urllib.request.Request(url, data=b"{}", headers=_auth_headers(args.api_key), method="POST")
            with urllib.request.urlopen(req) as resp:
                print(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"[ERROR] Unable to cancel task: {e}")


if __name__ == "__main__":
    main()
