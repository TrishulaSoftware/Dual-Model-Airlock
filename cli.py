"""
Dual-Model-Airlock CLI
Unified command line tool for dispatching, monitoring, and serving multi-model bridge tasks.
"""

import sys
import json
import argparse
import urllib.request
import urllib.error

from .airlock_core import AirlockTask
from .airlock_bridge import AirlockBridge
from .airlock_server import start_server


def check_health(host: str = "127.0.0.1", port: int = 9090):
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
        claude_bin = bridge._resolve_claude_binary()
        print(f"Local Claude Binary: {claude_bin or 'NOT FOUND'}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Dual-Model-Airlock: Gemini ↔ Claude Code Bridge")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Serve command
    p_serve = subparsers.add_parser("serve", help="Start the Airlock HTTP/IPC server")
    p_serve.add_argument("--port", "-p", type=int, default=9090, help="Port to listen on (default: 9090)")
    p_serve.add_argument("--host", type=str, default="127.0.0.1", help="Host interface")

    # Dispatch command
    p_disp = subparsers.add_parser("dispatch", help="Dispatch a task to a worker model")
    p_disp.add_argument("prompt", type=str, help="Instruction or prompt")
    p_disp.add_argument("--worker", "-w", type=str, default="claude", choices=["claude", "mock", "cmd"], help="Target worker")
    p_disp.add_argument("--commander", "-c", type=str, default="gemini", help="Originating commander")
    p_disp.add_argument("--async-run", "-a", action="store_true", help="Run asynchronously")
    p_disp.add_argument("--timeout", "-t", type=int, default=120, help="Timeout in seconds")
    p_disp.add_argument("--remote", action="store_true", help="Dispatch via running HTTP server instead of local bridge")
    p_disp.add_argument("--port", type=int, default=9090, help="Server port if using --remote")

    # Status command
    p_stat = subparsers.add_parser("status", help="Check task status")
    p_stat.add_argument("task_id", type=str, help="Task ID to inspect")
    p_stat.add_argument("--port", type=int, default=9090, help="Server port")

    # Check command
    p_chk = subparsers.add_parser("check", help="Verify local environment and server status")
    p_chk.add_argument("--port", type=int, default=9090, help="Server port")

    args = parser.parse_args()

    if args.command == "serve":
        server = start_server(port=args.port, host=args.host)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n[AIRLOCK] Server stopped.")

    elif args.command == "check":
        check_health(port=args.port)

    elif args.command == "dispatch":
        if args.remote:
            url = f"http://127.0.0.1:{args.port}/v1/dispatch"
            payload = json.dumps({
                "prompt": args.prompt,
                "worker": args.worker,
                "commander": args.commander,
                "timeout_sec": args.timeout,
                "async": args.async_run
            }).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
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
                timeout_sec=args.timeout
            )
            print(f"[AIRLOCK] Executing task {task.task_id} with worker '{args.worker}'...")
            res = bridge.execute_task(task, async_run=args.async_run)
            print(f"Status: {res.status.value} (exit code: {res.exit_code}, {res.duration_sec}s)")
            if res.stdout:
                print("\nSTDOUT:\n" + res.stdout)
            if res.stderr:
                print("\nSTDERR:\n" + res.stderr)

    elif args.command == "status":
        url = f"http://127.0.0.1:{args.port}/v1/task/{args.task_id}"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req) as resp:
                print(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"[ERROR] Unable to fetch task status: {e}")


if __name__ == "__main__":
    main()
