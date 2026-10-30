"""Deterministic subprocess fixture; does not invoke a model."""
import json
import sys


def emit(value):
    print(json.dumps(value), flush=True)


for line in sys.stdin:
    msg = json.loads(line)
    method = msg["method"]
    if method == "initialized":
        continue
    rid = msg["id"]
    if method == "initialize":
        result = {}
    elif method == "thread/start":
        result = {"thread": {"id": "thread-a"}}
    elif method == "thread/read":
        result = {"thread": {"id": msg["params"]["threadId"]}}
    elif method == "turn/start":
        text = msg["params"]["input"][0]["text"]
        tid = msg["params"]["threadId"]
        if text == "disconnect":
            sys.exit(0)
        result = {"turn": {"id": "turn-a"}}
        # Notifications may arrive before the turn/start response.
        emit({"method": "item/completed", "params": {"threadId": "other",
              "turnId": "turn-a", "item": {"type": "agentMessage", "text": "wrong"}}})
        emit({"method": "item/completed", "params": {"threadId": tid,
              "turnId": "turn-old", "item": {"type": "agentMessage", "text": "stale"}}})
        emit({"method": "item/completed", "params": {"threadId": tid,
              "turnId": "turn-a", "item": {"type": "agentMessage", "text": "hello received"}}})
        emit({"method": "turn/completed", "params": {"threadId": tid,
              "turn": {"id": "turn-a", "status": "failed" if text == "fail" else "completed"}}})
    else:
        emit({"id": rid, "error": {"code": -32601, "message": "unknown method"}})
        continue
    emit({"id": rid, "result": result})
