"""Bounded, redacted observations, separate from the broker's authoritative audit."""

import json
import time


def bounded(value, depth=0):
    if depth > 7:
        return "[nested content omitted]"
    if isinstance(value, str):
        return value if len(value) <= 2200 else value[:2200] + " … [truncated]"
    if isinstance(value, dict):
        return {str(k)[:80]: bounded(v, depth + 1) for k, v in list(value.items())[:25]}
    if isinstance(value, list):
        return [bounded(item, depth + 1) for item in value[-20:]]
    return value if value is None or type(value) in {bool, int, float} else str(value)[:200]


def model_input(request, private_read):
    if private_read:
        return {"messages": "[conversation withheld after private read]",
                "message_count": len(request.get("messages", [])), "model": request.get("model")}
    # No model-internal reasoning fields are included in the observer.
    return {"model": request.get("model"), "message_count": len(request.get("messages", [])), "messages": [
        {key: message[key] for key in ("role", "content", "tool_name", "tool_calls") if key in message}
        for message in request.get("messages", []) if isinstance(message, dict)]}


def model_output(response, private_read):
    if not isinstance(response, dict):
        return {"error": "Malformed model response"}
    message = response.get("message", {})
    if not isinstance(message, dict):
        return {"error": "Malformed model response"}
    calls = message.get("tool_calls") or []
    if not isinstance(calls, list):
        calls = []
    if private_read:
        return {"content": "[model text withheld after private read]", "tool_calls": [
            {"name": call.get("function", {}).get("name") if isinstance(call.get("function", {}).get("name"), str) and call.get("function", {}).get("name") in
             {"read_issue", "read_private_fixture", "post_public_comment"} else "other",
             "arguments": "[withheld after private read]"}
            for call in calls if isinstance(call, dict) and isinstance(call.get("function"), dict)]}
    return {key: message[key] for key in ("content", "tool_calls") if key in message}


class Trace:
    def __init__(self, path):
        self.path = path
        self.sequence = 0

    def emit(self, actor, kind, title, detail=None, **context):
        self.sequence += 1
        event = {"seq": self.sequence, "time": time.time(), "actor": actor, "kind": kind,
                 "title": title, "detail": bounded(detail), **context}
        raw = json.dumps(event, ensure_ascii=True)
        if len(raw) > 18000:
            event["detail"] = {"preview": json.dumps(event["detail"], ensure_ascii=True)[:12000], "truncated": True}
            raw = json.dumps(event, ensure_ascii=True)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(raw + "\n")
            file.flush()

    def for_run(self, run, mode, case):
        return lambda actor, kind, title, detail=None: self.emit(
            actor, kind, title, detail, run=run, mode=mode, case=case)
