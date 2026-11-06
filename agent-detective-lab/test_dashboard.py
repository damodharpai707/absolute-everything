"""Dashboard API regression tests. No real model requests or GitHub mutations."""

import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import dashboard


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = dashboard.Dashboard(Path(self.temp.name) / "results")
        self.addCleanup(self.app.close)

    def wait_job(self):
        for _ in range(100):
            job = self.app.snapshot()
            if job["status"] != "running":
                return job
            time.sleep(.01)
        self.fail("Background task did not finish")

    def test_health_checks_exact_installed_model(self):
        def model(path, *args, **kwargs):
            if path == "/api/tags":
                return {"models": [{"name": "qwen2.5:3b"}]}
            return {"version": "test-version"}
        with patch.object(dashboard, "ollama_request", side_effect=model):
            health = self.app.health()
        self.assertTrue(health["ollama"])
        self.assertTrue(health["model"])
        self.assertEqual(health["version"], "test-version")

    def test_probe_displays_model_reply_and_failures(self):
        with patch.object(dashboard, "ollama_request", return_value={"message": {"content": "Hello from test model"}}) as inference:
            self.app.start("probe", {})
            job = self.wait_job()
            self.assertEqual(job["exit_code"], 0)
            self.assertIn("Hello from test model", job["log"])
            self.assertEqual(inference.call_args.args[0], "/api/chat")
        with patch.object(dashboard, "ollama_request", side_effect=ConnectionRefusedError("Server unavailable")):
            self.app.start("probe", {})
            self.assertEqual(self.wait_job()["status"], "attention")

    def test_public_confirmation_and_valid_target_required(self):
        base = {"repo": "owner/test", "pr": 1, "token": "test-credential", "confirm_public": True}
        with patch.object(dashboard.sys, "platform", "linux"):
            for override in ({"confirm_public": False}, {"confirm_public": "true"},
                             {"repo": "owner/test;echo"}, {"repo": "owner/.."},
                             {"pr": True}, {"pr": 0}, {"token": "bad\ncredential"}):
                with self.subTest(override=override):
                    with self.assertRaises(ValueError):
                        self.app.start("experiment", base | override)
        self.assertIsNone(self.app.snapshot())

    def test_live_job_passes_token_only_to_child_and_preserves_exit_code(self):
        with patch.object(dashboard.sys, "platform", "linux"), patch.object(self.app, "command", return_value=3) as command:
            self.app.start("experiment", {"repo": "owner/test", "pr": 2, "token": "test-private-value", "confirm_public": True})
            job = self.wait_job()
            argv, token = command.call_args.args
            self.assertEqual(token, "test-private-value")
            self.assertNotIn(token, " ".join(argv))
            self.assertIn("owner/test", argv)
            self.assertEqual(job["exit_code"], 3)
            self.assertNotIn(token, json.dumps(job))

    def test_process_output_redacts_token_and_has_real_exit_code(self):
        self.app.job = {"log": ""}
        code = self.app.command([sys.executable, "-c", "import os; print(os.environ['DETECTIVE_GITHUB_TOKEN']); raise SystemExit(3)"], "test-redaction-value", timeout=10)
        self.assertEqual(code, 3)
        self.assertIn("[redacted]", self.app.job["log"])
        self.assertNotIn("test-redaction-value", self.app.job["log"])

    def test_only_one_job_can_run(self):
        entered, release = threading.Event(), threading.Event()
        def slow_model(*args, **kwargs):
            entered.set()
            release.wait(3)
            return {"message": {"content": "Done"}}
        with patch.object(dashboard, "ollama_request", side_effect=slow_model):
            self.app.start("probe", {})
            self.assertTrue(entered.wait(1))
            try:
                with self.assertRaises(RuntimeError):
                    self.app.start("pull-model", {})
            finally:
                release.set()
                self.wait_job()

    def test_windows_supports_model_but_rejects_linux_experiment(self):
        with patch.object(dashboard.sys, "platform", "win32"):
            for action in ("tests", "sandbox", "experiment"):
                with self.assertRaisesRegex(ValueError, "needs Linux"):
                    self.app.start(action, {})

    def test_reports_reject_traversal_and_external_symlinks(self):
        folder = self.app.results / "012345abcdef"
        folder.mkdir(parents=True)
        (folder / "results.json").write_text(json.dumps({"experiment": folder.name, "runs": []}))
        self.assertEqual(len(self.app.reports()), 1)
        with self.assertRaises(ValueError):
            self.app.read_report("../outside")
        external = Path(self.temp.name) / "outside.json"
        external.write_text('{"runs": []}')
        (folder / "results.json").unlink()
        try:
            (folder / "results.json").symlink_to(external)
        except OSError:
            self.skipTest("Symlinks unavailable")
        with self.assertRaises(ValueError):
            self.app.read_report(folder.name)
        self.assertEqual(self.app.reports(), [])


class DashboardHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.app = dashboard.Dashboard(cls.temp.name)
        cls.server = dashboard.Server(("127.0.0.1", 0), cls.app)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        cls.app.close()
        cls.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            res = conn.getresponse()
            return res.status, dict(res.getheaders()), res.read()
        finally:
            conn.close()

    def test_page_loads_with_session_token_and_content_policy(self):
        code, headers, body = self.request("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn(self.app.token.encode(), body)
        self.assertNotIn(b"__LAB_TOKEN__", body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(self.request("GET", "/app.js")[0], 200)

    def test_api_rejects_missing_token_cross_origin_and_bad_host(self):
        body = json.dumps({"action": "probe"})
        headers = {"Content-Type": "application/json"}
        self.assertEqual(self.request("POST", "/api/actions", body, headers)[0], 403)
        headers["X-Lab-Token"] = self.app.token
        headers["Origin"] = "https://untrusted.example"
        self.assertEqual(self.request("POST", "/api/actions", body, headers)[0], 403)
        self.assertEqual(self.request("GET", "/", headers={"Host": "untrusted.example"})[0], 403)
        self.assertEqual(self.request("GET", "/api/state")[0], 403)
        self.assertEqual(self.request("GET", "/api/state", headers={"X-Lab-Token": "é"})[0], 403)

    def test_real_http_request_runs_fixed_model_action_and_returns_state(self):
        headers = {"Content-Type": "application/json", "X-Lab-Token": self.app.token}
        with patch.object(dashboard, "ollama_request", return_value={"message": {"content": "HTTP probe succeeded"}}):
            code, _, _ = self.request("POST", "/api/actions", json.dumps({"action": "probe"}), headers)
            self.assertEqual(code, 202)
            for _ in range(100):
                _, _, raw = self.request("GET", "/api/state", headers=headers)
                state = json.loads(raw)
                if state["job"]["status"] != "running":
                    break
                time.sleep(.01)
            self.assertEqual(state["job"]["exit_code"], 0)
            self.assertIn("HTTP probe succeeded", state["job"]["log"])

    def test_invalid_body_and_unknown_actions_are_rejected(self):
        headers = {"Content-Type": "application/json", "X-Lab-Token": self.app.token}
        for body in ("[]", "not-json", '{"action":"execute-shell"}', '{"action":42}'):
            with self.subTest(body=body):
                self.assertEqual(self.request("POST", "/api/actions", body, headers)[0], 400)


if __name__ == "__main__":
    unittest.main()
