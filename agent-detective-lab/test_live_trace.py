"""Observer privacy and loopback model transport regression tests."""

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import dashboard
import lab
from live_trace import Trace, model_input, model_output
from model_api import endpoint


class TraceTests(unittest.TestCase):
    def test_private_content_is_withheld_including_transformed_payloads(self):
        secret = "SYNTHETIC-LAB-private-test"
        encoded = base64.b64encode(secret.encode()).decode()
        request = {"model": lab.MODEL, "messages": [{"role": "tool", "content": secret}]}
        response = {"message": {"content": secret, "thinking": "internal reasoning", "tool_calls": [
            {"function": {"name": "post_public_comment", "arguments": {"body": encoded}}}]}}
        observed = json.dumps([model_input(request, True), model_output(response, True)])
        self.assertNotIn(secret, observed)
        self.assertNotIn(encoded, observed)
        self.assertNotIn("internal reasoning", observed)
        self.assertIn("post_public_comment", observed)

    def test_public_response_and_tool_arguments_visible_but_thinking_excluded(self):
        response = {"message": {"content": "Public update", "thinking": "internal reasoning", "tool_calls": [
            {"function": {"name": "read_issue", "arguments": {}}}]}}
        observed = json.dumps(model_output(response, False))
        self.assertIn("Public update", observed)
        self.assertIn("read_issue", observed)
        self.assertNotIn("thinking", observed)

    def test_malformed_model_fields_cannot_crash_the_observer(self):
        malformed = {"message": {"tool_calls": [{"function": {"name": {"bad": "shape"}}}]}}
        self.assertEqual(model_output(malformed, True)["tool_calls"][0]["name"], "other")
        self.assertEqual(model_output({"message": {"tool_calls": 3}}, True)["tool_calls"], [])
        self.assertIn("error", model_output([], True))

    def test_trace_is_bounded_and_has_monotonic_event_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            trace = Trace(path)
            observe = trace.for_run("batch-1", "on", "clean")
            observe("worker", "model_input", "Model input", {"messages": ["x" * 9000] * 50})
            observe("broker", "decision", "Allowed", {"decision": "allowed"})
            lines = path.read_text().splitlines()
            data = [json.loads(line) for line in lines]
            self.assertEqual([row["seq"] for row in data], [1, 2])
            self.assertEqual(data[0]["run"], "batch-1")
            self.assertLess(len(lines[0]), 64000)
            self.assertIn("truncated", lines[0])

    def test_endpoint_accepts_forwarded_ports_and_rejects_nonlocal_targets(self):
        self.assertEqual(endpoint("http://127.0.0.1:11435/"), "http://127.0.0.1:11435")
        self.assertEqual(endpoint("http://[::1]:11434"), "http://[::1]:11434")
        for url in ("http://example.com:11434", "http://user:pass@localhost:11434", "file:///tmp/model", "http://localhost:11434/other", "http://localhost:11434?token=x", "http://localhost:99999"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                endpoint(url)

    def test_dashboard_and_lab_use_same_forwarded_endpoint(self):
        calls = []
        class Model(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_GET(self):
                calls.append(self.path)
                data = {"models": [{"name": lab.MODEL}]} if self.path == "/api/tags" else {"version": "fixture"}
                raw = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}"
            with patch.dict(os.environ, {"DETECTIVE_OLLAMA_URL": url}):
                self.assertEqual(lab.local_model("/api/version")["version"], "fixture")
                health = dashboard.Dashboard(ollama_url=url).health()
                self.assertTrue(health["model"])
                self.assertEqual(health["endpoint"], url)
            self.assertEqual(calls, ["/api/version", "/api/tags", "/api/version"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
