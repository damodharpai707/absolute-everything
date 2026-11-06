"""Launch the stdio-only lab agent on Linux with rootless bubblewrap (0.9+)."""

from functools import lru_cache
import json
from pathlib import Path
import re
import socket
import subprocess
import sys


_SYSTEM_ENV = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}
_PYTHON = "/usr/bin/python3"
_BWRAP = "/usr/bin/bwrap"


@lru_cache(maxsize=1)
def _base_command():
    if sys.platform != "linux" or not Path(_BWRAP).is_file():
        raise RuntimeError("This lab requires Linux and /usr/bin/bwrap (bubblewrap 0.9+).")
    # Ask the trusted system interpreter for its actual stdlib locations. Only
    # the interpreter, its linked libraries, and its stdlib enter the sandbox.
    # Some distributions build _socket, _json and select into the executable,
    # others load them as extension modules with their own ELF dependencies.
    probe = (
        'import json, socket, sys, sysconfig; '
        'roots = [sysconfig.get_path("stdlib"), sysconfig.get_path("platstdlib"), '
        '         sysconfig.get_config_var("DESTSHARED")]; '
        'modules = sorted({m.__file__ for m in sys.modules.values() '
        '                  if (getattr(m, "__file__", None) or "").endswith(".so")}); '
        'print(json.dumps([roots, modules]))'
    )
    try:
        paths, modules = json.loads(subprocess.check_output(
            [_PYTHON, "-I", "-S", "-c", probe], text=True, env=_SYSTEM_ENV, timeout=10,
        ))
        dependencies = subprocess.check_output(
            ["/usr/bin/ldd", _PYTHON, *modules], text=True, env=_SYSTEM_ENV, timeout=10,
        )
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise RuntimeError("Could not locate the distro's system Python 3 and glibc libraries.") from exc
    if 'not found' in dependencies:
        raise RuntimeError("System Python has unresolved shared libraries.")
    libs = re.findall(r"(?:=>\s+|^\s*)(/\S+)\s+\(", dependencies, re.MULTILINE)
    if not libs:
        raise RuntimeError("Could not determine the system Python runtime libraries.")
    command = [
        _BWRAP, "--unshare-all", "--unshare-user", "--disable-userns", "--cap-drop", "ALL",
        "--die-with-parent", "--new-session", "--clearenv", "--setenv", "LC_ALL", "C",
        "--dir", "/app", "--chdir", "/app",
    ]
    # Bind files at the loader's expected names, including symlink aliases.
    roots = sorted({Path(p) for p in paths if p and Path(p).is_dir()}, key=lambda p: len(p.parts))
    for path in (p for p in roots if not any(q in p.parents for q in roots)):
        command += ["--ro-bind", str(path.resolve()), str(path)]
    for path in sorted(set(libs + [_PYTHON])):
        command += ["--ro-bind", str(Path(path).resolve(strict=True)), path]
    return tuple(command)


@lru_cache(maxsize=1)
def selfcheck():
    """Require a working namespace and verify that the host loopback is unreachable."""
    command = list(_base_command())
    # A listener in the parent namespace supplies a positive target even when
    # the machine is offline. No real credentials are used for the probe.
    with socket.socket() as host:
        host.bind(("127.0.0.1", 0))
        host.listen(1)
        port = host.getsockname()[1]
        check = (
            "import os,socket,sys,json\n"
            "assert set(os.environ) <= {'LC_ALL','LC_CTYPE','PWD'}\n"
            "assert os.environ.get('PWD','/app') == '/app'\n"
            "assert all(not os.path.exists(p) for p in ('/home','/root','/proc','/run','/etc/passwd'))\n"
            "s=socket.socket(); s.settimeout(2)\n"
            "try:\n s.connect(('127.0.0.1',int(sys.argv[1])))\n"
            "except OSError:\n pass\n"
            "else:\n raise RuntimeError('host loopback reachable')\n"
            "print('isolated')\n"
        )
        try:
            result = subprocess.run(
                command + ["--remount-ro", "/", "--", _PYTHON, "-I", "-S", "-c", check, str(port)],
                env=_SYSTEM_ENV, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("Sandbox preflight failed. Check Linux user namespaces and bubblewrap.") from exc
    if result.returncode or result.stdout.strip() != "isolated":
        # Only the probe runs here. The bwrap diagnostic contains no agent input.
        detail = result.stderr.strip().splitlines()[-1:] or ["unexpected sandbox output"]
        raise RuntimeError("Sandbox preflight failed. Check Linux user namespaces and bubblewrap: " + detail[0])


def start_worker(worker_path: Path) -> subprocess.Popen:
    """Start an isolated worker. The caller owns its pipes and must stop it."""
    selfcheck()
    script = worker_path.resolve(strict=True)
    return subprocess.Popen(
        [*_base_command(), "--ro-bind", str(script), "/app/worker.py", "--remount-ro", "/",
         "--", _PYTHON, "-I", "-S", "-B", "-u", "/app/worker.py"],
        env=_SYSTEM_ENV, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1, close_fds=True,
    )


def stop_worker(process: subprocess.Popen):
    """Terminate the namespace and reap the bubblewrap child after any trial."""
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    finally:
        for pipe in (process.stdin, process.stdout):
            if pipe is not None:
                pipe.close()
