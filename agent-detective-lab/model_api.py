"""Ollama transport shared by the experiment and its observer."""

import http.client
import json
import os
from urllib.parse import urlsplit


def endpoint(value=None):
    value = value or os.environ.get("DETECTIVE_OLLAMA_URL", "http://127.0.0.1:11434")
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment):
        raise ValueError("Use a loopback Ollama URL, for example http://127.0.0.1:11434 or an SSH-forwarded port.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not 1 <= port <= 65535:
        raise ValueError("Invalid Ollama port")
    host = f"[{parsed.hostname}]" if parsed.hostname == "::1" else parsed.hostname
    return f"{parsed.scheme}://{host}:{port}"


def request(path, payload=None, timeout=180, base_url=None):
    parsed = urlsplit(endpoint(base_url))
    connection_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    connection = connection_type(parsed.hostname, parsed.port, timeout=timeout)
    try:
        body = None if payload is None else json.dumps(payload).encode()
        connection.request("GET" if body is None else "POST", path, body,
                           {"Content-Type": "application/json"})
        response = connection.getresponse()
        data = response.read(1_000_001)
        if response.status != 200 or len(data) > 1_000_000:
            raise RuntimeError(f"Ollama returned HTTP {response.status} or an oversized response.")
        return json.loads(data)
    finally:
        connection.close()
