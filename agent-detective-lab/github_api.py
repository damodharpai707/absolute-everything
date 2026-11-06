"""A GitHub Issues client bound to one explicitly named repository."""

import http.client
import json
import re
import urllib.error
import urllib.request


class GitHubError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _issue_path(number: int) -> str:
    if type(number) is not int or number < 1:
        raise ValueError("Expected a positive GitHub issue number")
    return f"/issues/{number}"


class GitHubRepo:
    def __init__(self, repo: str, token: str | None):
        owner, _, name = repo.partition("/")
        if (not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", owner)
                or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", name)
                or name in {".", ".."}):
            raise ValueError("Expected GitHub repository as owner/name")
        if token is not None and not re.fullmatch(r"[!-~]+", token):
            raise ValueError("Invalid GitHub credential format")
        self.repo = repo
        self._base = f"https://api.github.com/repos/{repo}"
        self._token = token
        self._http = urllib.request.build_opener(_NoRedirect())

    def _request(self, path, body=None):
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "agent-detective-lab"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        data = None
        if body is not None:
            if not self._token:
                raise GitHubError("Writing to GitHub requires a scoped repository token")
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self._base + path, data=data, headers=headers)
        try:
            with self._http.open(request, timeout=25) as response:
                limit = 16 * 1024 * 1024
                raw = response.read(limit + 1)
                if len(raw) > limit:
                    raise GitHubError("GitHub response exceeds the experiment limit")
                return json.loads(raw), response.headers.get("Link", "")
        except urllib.error.HTTPError as exc:
            exc.close()
            raise GitHubError(f"GitHub returned HTTP {exc.code}") from None
        except (OSError, http.client.HTTPException, ValueError):
            raise GitHubError("GitHub request or response could not be verified") from None

    def _object(self, path, body=None) -> dict:
        result, _ = self._request(path, body)
        if not isinstance(result, dict):
            raise GitHubError("Unexpected GitHub object response")
        return result

    def public_check(self) -> None:
        repo = self._object("")
        if (repo.get("private") is not False or repo.get("visibility") != "public"
                or str(repo.get("full_name", "")).lower() != self.repo.lower()):
            raise GitHubError("The configured repository could not be verified as public")

    def create_issue(self, title: str, body: str) -> dict:
        return self._object("/issues", {"title": title, "body": body})

    def read_issue(self, number: int) -> dict:
        return self._object(_issue_path(number))

    def post_comment(self, number: int, body: str) -> dict:
        return self._object(_issue_path(number) + "/comments", {"body": body})

    def list_comments(self, number: int) -> list[dict]:
        path = _issue_path(number) + "/comments"
        comments = []
        for page in range(1, 21):
            batch, links = self._request(f"{path}?per_page=100&page={page}")
            if not isinstance(batch, list) or not all(isinstance(item, dict) for item in batch):
                raise GitHubError("Unexpected GitHub comment list")
            comments.extend(batch)
            # Only use the pagination signal. Never send credentials to a returned URL.
            if 'rel="next"' not in links:
                return comments
        raise GitHubError("GitHub comment history exceeds the verification limit")
