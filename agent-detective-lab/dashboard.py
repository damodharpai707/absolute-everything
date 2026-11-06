#!/usr/bin/env python3
"""Local, dependency-free web dashboard for Agent Detective Lab."""

import argparse
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from urllib.parse import parse_qs, urlsplit

from model_api import endpoint, request as model_request

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "web"
MODEL = "qwen2.5:3b"
REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}\Z")
BATCH = re.compile(r"[a-f0-9]{12}\Z")


def ollama_request(path, payload=None, timeout=4, base_url=None):
    return model_request(path, payload, timeout=timeout, base_url=base_url)


class Dashboard:
    def __init__(self, results=None, ollama_url=None):
        self.results = Path(results) if results else HERE / "results"
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.job = None
        self.process = None
        self.ollama_url = endpoint(ollama_url)
        self.closed = False

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.job))

    def note(self, text):
        with self.lock:
            self.job["log"] = (self.job["log"] + str(text))[-24000:]

    def health(self):
        data = {"linux": sys.platform == "linux", "python": sys.version.split()[0],
                "ollama": False, "model": False, "version": None,
                "endpoint": self.ollama_url,
                "bubblewrap": Path("/usr/bin/bwrap").is_file(),
                "credential": bool(os.environ.get("DETECTIVE_GITHUB_TOKEN")),
                "model_name": MODEL, "message": "Ollama is not reachable at the configured port."}
        try:
            tags = ollama_request("/api/tags", base_url=self.ollama_url)
            data["model"] = any(m.get("name") == MODEL for m in tags.get("models", []))
            data["ollama"] = True
            data["message"] = "Connected to the configured Ollama endpoint."
            try:
                data["version"] = ollama_request("/api/version", base_url=self.ollama_url).get("version")
            except (OSError, ValueError, RuntimeError, http.client.HTTPException):
                pass
        except (OSError, ValueError, AttributeError, TypeError, RuntimeError, http.client.HTTPException):
            pass
        return data

    def reports(self):
        reports = []
        if not self.results.exists():
            return reports
        for folder in self.results.iterdir():
            if not BATCH.fullmatch(folder.name) or folder.is_symlink():
                continue
            path = folder / "results.json"
            try:
                data = self.read_report(folder.name)
                reports.append({"id": folder.name, "repo": data.get("repo", ""),
                                "verification": data.get("verification", "pending"),
                                "modified": path.stat().st_mtime})
            except (OSError, ValueError):
                continue
        return sorted(reports, key=lambda row: row["modified"], reverse=True)

    def read_report(self, batch):
        if not BATCH.fullmatch(batch):
            raise ValueError("Invalid report ID")
        path = self.results / batch / "results.json"
        if path.is_symlink() or not path.resolve().is_relative_to(self.results.resolve()):
            raise ValueError("Invalid report path")
        if path.stat().st_size > 2_000_000:
            raise ValueError("Report is too large")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("runs"), list):
            raise ValueError("Invalid experiment report")
        return data

    def read_trace(self, batch, after=0):
        if not BATCH.fullmatch(batch) or after < 0:
            raise ValueError("Invalid trace request")
        path = self.results / batch / "trace.jsonl"
        if path.is_symlink() or not path.resolve().is_relative_to(self.results.resolve()):
            raise ValueError("Invalid trace path")
        if not path.exists():
            return {"events": [], "cursor": after, "available": False}
        if path.stat().st_size > 32_000_000:
            raise ValueError("Trace is too large")
        events = []
        with path.open(encoding="utf-8") as file:
            for raw in file:
                if not raw.endswith("\n"):
                    break  # A producer may still be writing its final line.
                if len(raw) > 64000:
                    continue
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(event, dict) and type(event.get("seq")) is int and event["seq"] > after:
                    events.append(event)
                    if len(events) == 200:
                        break
        return {"events": events, "cursor": events[-1]["seq"] if events else after, "available": True}

    def start(self, action, data):
        if action != "experiment":
            raise ValueError("Unknown action")
        if sys.platform != "linux":
            raise ValueError("This action needs Linux. Open the dashboard on Spark, or use GitHub Actions.")
        # Credentials stay in the request/worker closure, never in job state or files.
        token = None
        args = {}
        if action == "experiment":
            repo, pr = data.get("repo", ""), data.get("pr")
            if not isinstance(repo, str) or not REPO.fullmatch(repo) or repo.split("/")[1] in {".", ".."}:
                raise ValueError("Enter a repository as owner/name.")
            if type(pr) is not int or not 1 <= pr <= 2_147_483_647:
                raise ValueError("Enter a positive receiver PR number.")
            if data.get("confirm_public") is not True:
                raise ValueError("Confirm that this run creates public issues and comments.")
            token = data.get("token") or os.environ.get("DETECTIVE_GITHUB_TOKEN")
            if not isinstance(token, str) or not re.fullmatch(r"[!-~]{1,4096}", token):
                raise ValueError("Provide a scoped GitHub token or set DETECTIVE_GITHUB_TOKEN before starting the dashboard.")
            args = {"repo": repo, "pr": pr}
        with self.lock:
            if self.closed:
                raise RuntimeError("The dashboard is shutting down.")
            if self.job and self.job["status"] == "running":
                raise RuntimeError("Another task is running. Wait for it to finish.")
            self.job = {"id": secrets.token_hex(8), "action": action, "status": "running",
                        "started": time.time(), "log": "", "exit_code": None}
            threading.Thread(target=self._run, args=(action, args, token), daemon=True).start()
            return self.snapshot()

    def command(self, argv, token=None, timeout=6600):
        environment = os.environ.copy()
        environment.pop("DETECTIVE_GITHUB_TOKEN", None)
        environment["DETECTIVE_OLLAMA_URL"] = self.ollama_url
        if token:
            environment["DETECTIVE_GITHUB_TOKEN"] = token
        with self.lock:
            if self.closed:
                raise RuntimeError("Dashboard closed")
            proc = subprocess.Popen(argv, cwd=HERE, env=environment, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace", bufsize=1)
            self.process = proc
        timed_out = threading.Event()

        def expire():
            timed_out.set()
            if proc.poll() is None:
                proc.kill()

        timer = threading.Timer(timeout, expire)
        timer.daemon = True
        timer.start()
        try:
            for line in proc.stdout:
                self.note(line.replace(token, "[redacted]") if token else line)
            code = proc.wait()
            if timed_out.is_set():
                self.note("\nTask timed out. Check service status before trying again.\n")
            return code
        finally:
            timer.cancel()
            proc.stdout.close()
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            with self.lock:
                if self.process is proc:
                    self.process = None

    def _run(self, action, args, token):
        code = 0
        try:
            if action == "experiment":
                self.note("Starting six trials. Follow progress below; public writes begin after preflight checks.\n")
                code = self.command([sys.executable, "-B", "-u", str(HERE / "lab.py"), "run",
                    "--repo", args["repo"], "--pr", str(args["pr"]), "--out", str(self.results)], token)
        except (OSError, ValueError, RuntimeError, http.client.HTTPException) as exc:
            # Never expose raw HTTP bodies, request data, or the token.
            message = str(exc)
            self.note("\n" + (message.replace(token, "[redacted]") if token else message) + "\n")
            code = 1
        except Exception:
            self.note("\nTask could not finish. Check the service and try again.\n")
            code = 1
        finally:
            token = None
            with self.lock:
                self.job.update(status="complete" if code == 0 else "attention", exit_code=code, finished=time.time())

    def close(self):
        with self.lock:
            self.closed = True
            for proc in (self.process,):
                if proc is not None and proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, app):
        self.app = app
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, data, code=200, mime="application/json"):
        if mime == "application/json":
            data = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(data)

    def allowed_host(self):
        port = self.server.server_port
        return self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def authorized(self):
        port = self.server.server_port
        origin = self.headers.get("Origin")
        candidate = self.headers.get("X-Lab-Token", "")
        return (self.allowed_host() and (not origin or origin in
                {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}) and
                candidate.isascii() and secrets.compare_digest(candidate, self.server.app.token))

    def do_GET(self):
        if not self.allowed_host():
            return self.respond({"error": "Use the dashboard's localhost address."}, 403)
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8"),
                  "/icon.svg": ("icon.svg", "image/svg+xml")}
        if self.path in assets:
            name, mime = assets[self.path]
            data = (ASSETS / name).read_bytes()
            if name == "index.html":
                data = data.replace(b"__LAB_TOKEN__", self.server.app.token.encode())
            return self.respond(data, mime=mime)
        if not self.authorized():
            return self.respond({"error": "Reload the dashboard to reconnect."}, 403)
        try:
            if self.path == "/api/health":
                return self.respond(self.server.app.health())
            if self.path == "/api/state":
                return self.respond({"job": self.server.app.snapshot(), "reports": self.server.app.reports()})
            if self.path.startswith("/api/traces/"):
                parsed = urlsplit(self.path)
                after = int(parse_qs(parsed.query).get("after", ["0"])[0])
                return self.respond(self.server.app.read_trace(parsed.path.removeprefix("/api/traces/"), after))
            if self.path.startswith("/api/reports/"):
                return self.respond(self.server.app.read_report(self.path.removeprefix("/api/reports/")))
        except (OSError, ValueError):
            return self.respond({"error": "This report could not be read."}, 404)
        self.respond({"error": "Not found"}, 404)

    def do_POST(self):
        if not self.authorized():
            return self.respond({"error": "Reload the dashboard to reconnect."}, 403)
        if self.path != "/api/actions":
            return self.respond({"error": "Not found"}, 404)
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            return self.respond({"error": "JSON required"}, 415)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 12000:
                raise ValueError("Invalid request size")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict) or not isinstance(data.get("action"), str):
                raise ValueError("Invalid action request")
            job = self.server.app.start(data["action"], data)
            self.respond({"job": job}, 202)
        except (ValueError, TypeError):
            self.respond({"error": "Check the action, repository, PR number, token, and public-post confirmation."}, 400)
        except RuntimeError as exc:
            self.respond({"error": str(exc)}, 409)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--ollama-url", type=endpoint, help="Ollama loopback endpoint, including a forwarded laptop port")
    parser.add_argument("--results", type=Path, help="Watch a custom experiment results directory")
    parser.add_argument("--no-browser", action="store_true", help="Use when connecting to Spark over SSH")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535")
    app = Dashboard(results=args.results, ollama_url=args.ollama_url)
    try:
        server = Server(("127.0.0.1", args.port), app)
    except OSError as exc:
        parser.exit(1, f"Cannot open the dashboard port: {exc}\n")
    url = f"http://127.0.0.1:{args.port}"
    print(f"Agent Detective Lab → {url}\nKeep this terminal open. Press Ctrl+C to stop.", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
        server.server_close()


if __name__ == "__main__":
    main()
