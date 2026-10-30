"""Newline-delimited App Server JSON-RPC, with injectable subprocess transport."""
import asyncio
import contextlib
import json
from collections import deque


class RpcError(RuntimeError):
    pass


class AppServer:
    """One connection, serialized calls/turns. Never expose this directly to peers.

    argv must target a JSONL stdio server. `codex app-server proxy` forwards raw
    Unix-socket bytes and requires a WebSocket handshake; it is not compatible.
    This client does not discover or attach to existing sessions.
    """

    def __init__(self, argv=None, timeout=120, *, experimental=False, env=None):
        self.argv = argv or ["codex", "app-server", "--stdio"]
        self.timeout = timeout
        self.env = env
        self.experimental = experimental
        self.process = None
        self.pending = {}
        self.events = asyncio.Queue(maxsize=4096)
        self.stderr_tail = deque(maxlen=20)
        self.counter = 0
        self.lock = asyncio.Lock()

    async def __aenter__(self):
        self.process = await asyncio.create_subprocess_exec(
            *self.argv, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=8 * 1024 * 1024, env=self.env,
        )
        self.reader = asyncio.create_task(self._read())
        self.err_reader = asyncio.create_task(self._stderr())
        try:
            await self.call("initialize", {
                "clientInfo": {"name": "codex_bridge", "version": "0.1.0"},
                "capabilities": {"experimentalApi": self.experimental},
            })
            await self._send({"method": "initialized"})
        except BaseException:
            await self.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(self, *args):
        if self.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    self.process.kill()
                await self.process.wait()
        for task in (self.reader, self.err_reader):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _send(self, value):
        self.process.stdin.write((json.dumps(value) + "\n").encode())
        await self.process.stdin.drain()

    async def _stderr(self):
        while line := await self.process.stderr.readline():
            self.stderr_tail.append(line.decode(errors="replace").rstrip())

    async def _read(self):
        failure = RpcError("App Server disconnected")
        try:
            while line := await self.process.stdout.readline():
                msg = json.loads(line)
                if "id" in msg and "method" not in msg:
                    future = self.pending.get(msg["id"])
                    if future and not future.done():
                        if "error" in msg:
                            future.set_exception(RpcError(json.dumps(msg["error"])))
                        else:
                            future.set_result(msg.get("result"))
                elif "id" in msg:
                    # Fail closed: no automatic approvals, shell, or tool execution.
                    await self._send({"id": msg["id"], "error": {
                        "code": -32601, "message": "Bridge PoC does not handle server requests",
                    }})
                else:
                    self.events.put_nowait(msg)
        except Exception as exc:
            failure = RpcError(f"App Server reader failed: {exc}")
        finally:
            self.failure = failure
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(failure)
            # Wake a waiting turn even when the server exits between notifications.
            if not self.events.full():
                self.events.put_nowait({"_failure": failure})

    async def call(self, method, params):
        if self.reader.done():
            raise self.failure
        self.counter += 1
        request_id = self.counter
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self._send({"id": request_id, "method": method, "params": params})
            return await asyncio.wait_for(future, self.timeout)
        finally:
            self.pending.pop(request_id, None)

    async def start_thread(self, cwd, *, developer_instructions=None):
        result = await self.call("thread/start", {
            "cwd": str(cwd), "sandbox": "read-only", "approvalPolicy": "never",
            "developerInstructions": developer_instructions or "This is a conversation continuity test. Do not use tools. Reply briefly.",
        })
        return result["thread"]["id"]

    async def turn(self, thread_id, text):
        async with self.lock:
            result = await self.call("turn/start", {
                "threadId": thread_id,
                "input": [{"type": "text", "text": text, "text_elements": []}],
            })
            turn_id = result["turn"]["id"]
            output = []
            try:
                async with asyncio.timeout(self.timeout):
                    while True:
                        event = await self.events.get()
                        if "_failure" in event:
                            raise event["_failure"]
                        params = event.get("params", {})
                        if params.get("threadId") != thread_id:
                            continue
                        event_turn = params.get("turnId", params.get("turn", {}).get("id"))
                        if event_turn is not None and event_turn != turn_id:
                            continue
                        if event.get("method") == "item/completed":
                            item = params.get("item", {})
                            if item.get("type") == "agentMessage":
                                output.append(item["text"])
                        if event.get("method") == "turn/completed":
                            if params["turn"]["status"] != "completed":
                                raise RpcError(f"Turn failed: {params['turn']}")
                            return "\n".join(output)
            except (TimeoutError, asyncio.CancelledError):
                with contextlib.suppress(Exception):
                    await self.call("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})
                raise
