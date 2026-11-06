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
import shutil
import subprocess
import sys
import threading
import time
import webbrowser

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "web"
MODEL = "qwen2.5:3b"
REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}\Z")
BATCH = re.compile(r"[a-f0-9]{12}\Z")


def ollama_request(path, payload=None, timeout=4):
    connection = http.client.HTTPConnection("127.0.0.1", 11434, timeout=timeout)
    try:
        body = None if payload is None else json.dumps(payload).encode()
        connection.request("GET" if body is None else "POST", path, body,
                           {"Content-Type": "application/json"})
        response = connection.getresponse()
        data = response.read(1_000_001)
        if response.status != 200 or len(data) > 1_000_000:
            raise RuntimeError("Ollama could not complete the request. Check its service logs.")
        return json.loads(data)
    finally:
        connection.close()


class Dashboard:
    def __init__(self, results=None):
        self.results = Path(results) if results else HERE / "results"
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.job = None
        self.process = None
        self.model_process = None
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
                "ollama_cli": shutil.which("ollama") is not None,
                "bubblewrap": Path("/usr/bin/bwrap").is_file(),
                "credential": bool(os.environ.get("DETECTIVE_GITHUB_TOKEN")),
                "model_name": MODEL, "message": "Ollama is not reachable on this computer."}
        try:
            tags = ollama_request("/api/tags")
            data["model"] = any(m.get("name") == MODEL for m in tags.get("models", []))
            data["ollama"] = True
            data["message"] = "Connected to Ollama on this computer."
            try:
                data["version"] = ollama_request("/api/version").get("version")
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

    def start(self, action, data):
        if action not in {"start-model", "pull-model", "probe", "tests", "sandbox", "experiment"}:
            raise ValueError("Unknown action")
        if action in {"tests", "sandbox", "experiment"} and sys.platform != "linux":
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
            if action == "start-model":
                if self.health()["ollama"]:
                    self.note("Ollama is already running.\n")
                else:
                    cli = shutil.which("ollama")
                    if not cli:
                        raise RuntimeError("Install Ollama first, then click Start Ollama again.")
                    environment = os.environ.copy()
                    environment.pop("DETECTIVE_GITHUB_TOKEN", None)
                    environment.update(OLLAMA_NO_CLOUD="1", OLLAMA_HOST="127.0.0.1:11434")
                    with self.lock:
                        if self.closed:
                            raise RuntimeError("Dashboard closed")
                        if self.model_process and self.model_process.poll() is None:
                            raise RuntimeError("Ollama is still starting. Wait a moment and check connection.")
                        self.model_process = subprocess.Popen([cli, "serve"], env=environment,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    for _ in range(30):
                        if self.health()["ollama"]:
                            self.note("Ollama is ready. Download the model next.\n")
                            break
                        if self.model_process.poll() is not None:
                            raise RuntimeError("Ollama could not start. Run 'ollama serve' in a terminal to see its diagnostic output.")
                        time.sleep(1)
                    else:
                        raise RuntimeError("Ollama did not become ready. Check its service logs.")
            elif action == "pull-model":
                self.note("Downloading qwen2.5:3b. This may take several minutes.\n")
                ollama_request("/api/pull", {"model": MODEL, "stream": False}, timeout=3600)
                self.note("Model download finished.\n")
            elif action == "probe":
                self.note("Asking qwen2.5:3b for a short greeting…\n")
                reply = ollama_request("/api/chat", {"model": MODEL, "stream": False,
                    "messages": [{"role": "user", "content": "In one short sentence, greet someone opening an AI safety experiment lab."}],
                    "options": {"num_predict": 80, "temperature": 0}}, timeout=180)
                content = reply.get("message", {}).get("content")
                if not isinstance(content, str) or not content.strip():
                    raise RuntimeError("Ollama returned no text. Check that the model is working.")
                self.note(content.strip() + "\n")
            elif action == "tests":
                code = self.command([sys.executable, "-B", "-m", "unittest", "discover", "-v"], timeout=120)
            elif action == "sandbox":
                code = self.command([sys.executable, "-B", "-c",
                    'from isolation import selfcheck; selfcheck(); print("Sandbox ready: isolated filesystem and network.")'], timeout=30)
            elif action == "experiment":
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
            for proc in (self.process, self.model_process):
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
    parser.add_argument("--no-browser", action="store_true", help="Use when connecting to Spark over SSH")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535")
    app = Dashboard()
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
