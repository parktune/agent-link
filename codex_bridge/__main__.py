import argparse
import asyncio
import json
import tempfile
import uuid
from pathlib import Path

from .rpc import AppServer
from .cloud import cloud_probe, inspect_cloud
from .daemon import cloud_status


async def probe():
    """No model turn and no changes to existing conversations."""
    async with AppServer() as server:
        with tempfile.TemporaryDirectory(prefix="agent-link-probe-") as cwd:
            thread = await server.start_thread(cwd)
            result = await server.call("thread/read", {"threadId": thread, "includeTurns": False})
            assert result["thread"]["id"] == thread
            print(json.dumps({"initialize": "passed", "thread/start": "passed",
                              "thread/read": "passed", "model_called": False}))


async def demo():
    """Real model calls, two dedicated processes/threads, application-mediated round trips."""
    task_id = str(uuid.uuid4())

    def record(kind, sender, receiver, **data):
        value = {"type": kind, "task_id": task_id, "sender": sender,
                 "receiver": receiver, **data}
        print(json.dumps(value, ensure_ascii=False), flush=True)

    with tempfile.TemporaryDirectory(prefix="agent-link-demo-") as root:
        for name in ("A", "B"):
            Path(root, name).mkdir()
        async with AppServer() as a, AppServer() as b:
            ta = await a.start_thread(Path(root, "A"))
            tb = await b.start_thread(Path(root, "B"))
            if ta == tb:
                raise RuntimeError("Thread IDs must be distinct")
            marker_a, marker_b = uuid.uuid4().hex, uuid.uuid4().hex
            await a.turn(ta, f"Remember your private continuity marker: {marker_a}. Reply READY.")
            await b.turn(tb, f"Remember your private continuity marker: {marker_b}. Reply READY.")
            request = await b.turn(tb, "Ask Agent A to greet you. Reply exactly: hello")
            record("task_request", "B", "A", text=request, thread_id=tb)
            reply = await a.turn(ta, f"Agent B sent this test greeting: {json.dumps(request)}. Reply exactly: hello received")
            record("task_completed", "A", "B", text=reply, thread_id=ta)
            check_b = await b.turn(tb, f"Agent A replied: {json.dumps(reply)}. Return ONLY your private continuity marker from earlier.")
            check_a = await a.turn(ta, "Return ONLY your private continuity marker from earlier.")
            if marker_a not in check_a or marker_b not in check_b:
                raise RuntimeError("Continuity check failed")
            if marker_b in check_a or marker_a in check_b:
                raise RuntimeError("Conversation isolation check failed")
            if "hello received" not in reply.lower():
                raise RuntimeError("Greeting check failed")
            # Reverse direction: use the same threads and return the response to A.
            task_id = str(uuid.uuid4())
            reverse = await a.turn(ta, "Ask Agent B to greet you. Reply exactly: hello")
            record("task_request", "A", "B", text=reverse, thread_id=ta)
            reverse_reply = await b.turn(tb, f"Agent A sent: {json.dumps(reverse)}. Reply exactly: hello received")
            record("task_completed", "B", "A", text=reverse_reply, thread_id=tb)
            final = await a.turn(ta, f"Agent B replied: {json.dumps(reverse_reply)}. Return ONLY your private continuity marker.")
            if marker_a not in final or "hello received" not in reverse_reply.lower():
                raise RuntimeError("Reverse round trip failed")
            record("verification", "bridge", "user", distinct_threads=True,
                   context_retained=True, bidirectional=True,
                   network="local stdio", autonomous_tool_selection=False)


def main():
    parser = argparse.ArgumentParser(description="Agent Link: security (SSH) and comfort (reserved Tailscale) modes")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("probe", "demo", "cloud-inspect", "cloud-probe", "cloud-status"):
        child = commands.add_parser(command)
        child.add_argument("--timeout", type=float, default=30)
    commands.add_parser("modes")
    commands.add_parser("comfort", help="Tailscale mode: reserved, not implemented or installed")
    from .security_cli import configure, run
    configure(commands)
    args = parser.parse_args()
    if hasattr(args, "timeout") and not 0 < args.timeout <= 300:
        parser.error("--timeout must be greater than 0 and at most 300")
    try:
        if args.command == "security":
            raise SystemExit(run(args))
        elif args.command == "modes":
            print(json.dumps({"security": {"transport": "ssh", "available": True},
                              "comfort": {"transport": "tailscale", "available": False}}))
        elif args.command == "comfort":
            parser.exit(2, "comfort mode is reserved. Tailscale is not installed or configured by this command.\n")
        elif args.command == "cloud-inspect":
            print(json.dumps(asyncio.run(inspect_cloud())))
        elif args.command == "cloud-status":
            print(json.dumps(asyncio.run(cloud_status())))
        elif args.command == "cloud-probe":
            raise SystemExit(asyncio.run(cloud_probe(args.timeout)))
        else:
            asyncio.run(probe() if args.command == "probe" else demo())
    except (RuntimeError, TimeoutError, OSError, ValueError) as exc:
        parser.exit(1, f"agent-link: {exc}\n")


if __name__ == "__main__":
    main()
