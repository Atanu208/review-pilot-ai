"""In-memory fake of the GitHub web + REST APIs, served through ``httpx.MockTransport``."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, unquote

import httpx

API = "https://api.github.com"
WEB = "https://github.com"
DEFAULT_PATCH = "\n".join(["@@ -1 +1 @@", "-a", "+b"])


@dataclass
class FakeUser:
    id: int
    login: str
    name: str | None = None
    email: str | None = None


@dataclass
class FakeGitHub:
    users_by_token: dict[str, FakeUser] = field(default_factory=dict)
    codes: dict[str, str] = field(default_factory=dict)  # oauth code -> access token
    refresh_tokens: dict[str, str] = field(default_factory=dict)  # refresh -> new access token
    user_installations: dict[str, list[int]] = field(default_factory=dict)  # token -> ids
    installations: dict[int, dict[str, Any]] = field(default_factory=dict)
    installation_repos: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    expiring_tokens: bool = True
    requests: list[httpx.Request] = field(default_factory=list)
    pulls: dict[tuple[str, int], dict[str, Any]] = field(default_factory=dict)
    pull_files: dict[tuple[str, int], list[dict[str, Any]]] = field(default_factory=dict)
    # (repo full name, file path, commit sha) -> raw file bytes.
    contents: dict[tuple[str, str, str], bytes] = field(default_factory=dict)
    # Path substring -> queued responses returned before normal handling (failure injection).
    inject: dict[str, list[httpx.Response]] = field(default_factory=dict)
    # Submitted reviews per (repo, PR number); each has id, body, commit_id, comments.
    reviews: dict[tuple[str, int], list[dict[str, Any]]] = field(default_factory=dict)
    # Reject every review that carries inline comments (exercises the summary fallback).
    reject_inline_comments: bool = False

    # ---- helpers to build state --------------------------------------------------

    def add_user(self, user: FakeUser, code: str, token: str) -> None:
        self.users_by_token[token] = user
        self.codes[code] = token

    def add_installation(
        self, inst_id: int, account_login: str, repos: list[dict[str, Any]]
    ) -> None:
        self.installations[inst_id] = {
            "id": inst_id,
            "account": {"id": inst_id * 10, "login": account_login, "type": "Organization"},
            "repository_selection": "selected",
            "permissions": {"pull_requests": "write", "contents": "read", "metadata": "read"},
            "events": ["pull_request"],
            "suspended_at": None,
        }
        self.installation_repos[inst_id] = repos

    @staticmethod
    def repo(repo_id: int, full_name: str, private: bool = True) -> dict[str, Any]:
        return {
            "id": repo_id,
            "full_name": full_name,
            "private": private,
            "default_branch": "main",
            "language": "Python",
            "html_url": f"{WEB}/{full_name}",
        }

    def add_pull(
        self,
        full_name: str,
        number: int,
        *,
        head_sha: str = "b" * 40,
        state: str = "open",
        draft: bool = False,
        files: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        pr = {
            "id": 50_000 + number,
            "number": number,
            "title": f"PR {number}",
            "state": state,
            "draft": draft,
            "merged": False,
            "user": {"login": "dev", "avatar_url": None},
            "base": {"ref": "main", "sha": "a" * 40},
            "head": {"ref": "feature", "sha": head_sha},
            "html_url": f"{WEB}/{full_name}/pull/{number}",
            "created_at": "2026-11-15T10:00:00Z",
            "additions": 3,
            "deletions": 1,
            "changed_files": len(files or []),
        }
        self.pulls[(full_name, number)] = pr
        self.pull_files[(full_name, number)] = files if files is not None else []
        return pr

    @staticmethod
    def file(
        path: str,
        additions: int = 3,
        deletions: int = 1,
        *,
        patch: str | None = DEFAULT_PATCH,
        status: str = "modified",
    ) -> dict[str, Any]:
        return {
            "filename": path,
            "status": status,
            "additions": additions,
            "deletions": deletions,
            "changes": additions + deletions,
            "patch": patch,
        }

    def source_file(
        self,
        full_name: str,
        ref: str,
        path: str,
        content: str,
        *,
        added: set[int] | None = None,
        status: str = "modified",
    ) -> dict[str, Any]:
        """Register file content at ``ref`` and return a ``pulls/files`` entry whose patch
        marks ``added`` lines (default: every line) as added by the PR."""
        self.contents[(full_name, path, ref)] = content.encode()
        lines = content.splitlines()
        added = set(range(1, len(lines) + 1)) if added is None else added
        old_count = len(lines) - len(added)
        body = [("+" if i in added else " ") + line for i, line in enumerate(lines, start=1)]
        patch = "\n".join([f"@@ -1,{old_count} +1,{len(lines)} @@", *body])
        return {
            "filename": path,
            "status": status,
            "additions": len(added),
            "deletions": 0,
            "changes": len(added),
            "patch": patch,
        }

    def _repo_access(self, token: str, full_name: str) -> bool:
        inst = token.removeprefix("ghs_inst_")
        if not inst.isdigit():
            return False
        return any(r["full_name"] == full_name for r in self.installation_repos.get(int(inst), []))

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    # ---- request handling ----------------------------------------------------------

    def _bearer(self, request: httpx.Request) -> str:
        return str(request.headers.get("authorization", "")).removeprefix("Bearer ").strip()

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        path = request.url.path
        for fragment, queued in self.inject.items():
            if fragment in path and queued:
                return queued.pop(0)

        if url.startswith(f"{WEB}/login/oauth/access_token"):
            return self._token(request)
        if not url.startswith(API):
            return httpx.Response(404, json={"message": "Not Found"})

        token = self._bearer(request)
        if path == "/user":
            user = self.users_by_token.get(token)
            if user is None:
                return httpx.Response(401, json={"message": "Bad credentials"})
            return httpx.Response(
                200,
                json={
                    "id": user.id,
                    "login": user.login,
                    "name": user.name,
                    "email": user.email,
                    "avatar_url": f"https://avatars.githubusercontent.com/u/{user.id}",
                },
            )
        if path == "/user/installations":
            ids = self.user_installations.get(token, [])
            items = [self.installations[i] for i in ids if i in self.installations]
            return httpx.Response(200, json={"total_count": len(items), "installations": items})
        if path.startswith("/app/installations/") and path.endswith("/access_tokens"):
            inst_id = int(path.split("/")[3])
            if inst_id not in self.installations:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(
                201, json={"token": f"ghs_inst_{inst_id}", "expires_at": "2099-01-01T00:00:00Z"}
            )
        if path.startswith("/app/installations/"):
            inst = self.installations.get(int(path.split("/")[3]))
            return (
                httpx.Response(200, json=inst)
                if inst
                else httpx.Response(404, json={"message": "Not Found"})
            )
        if path == "/installation/repositories":
            inst_id = int(token.removeprefix("ghs_inst_"))
            repos = self.installation_repos.get(inst_id, [])
            return httpx.Response(200, json={"total_count": len(repos), "repositories": repos})
        if path.startswith("/repos/") and "/contents/" in path:
            parts = path.split("/", 5)  # ['', 'repos', owner, repo, 'contents', file path]
            full_name, file_path = f"{parts[2]}/{parts[3]}", unquote(parts[5])
            ref = request.url.params.get("ref", "")
            key = (full_name, file_path, ref)
            if not self._repo_access(token, full_name) or key not in self.contents:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, content=self.contents[key])
        if path.startswith("/repos/") and "/reviews" in path:
            return self._reviews(request, token, path)
        if path.startswith("/repos/") and "/pulls/" in path:
            parts = path.split("/")  # /repos/{owner}/{repo}/pulls/{n}[/files]
            full_name, number = f"{parts[2]}/{parts[3]}", int(parts[5])
            if not self._repo_access(token, full_name) or (full_name, number) not in self.pulls:
                return httpx.Response(404, json={"message": "Not Found"})
            if path.endswith("/files"):
                return httpx.Response(200, json=self.pull_files[(full_name, number)])
            return httpx.Response(200, json=self.pulls[(full_name, number)])
        if path.startswith("/repositories/"):
            inst_id = int(token.removeprefix("ghs_inst_") or 0)
            repo_id = int(path.split("/")[2])
            for repo in self.installation_repos.get(inst_id, []):
                if repo["id"] == repo_id:
                    return httpx.Response(200, json=repo)
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(404, json={"message": "Not Found"})

    def _reviews(self, request: httpx.Request, token: str, path: str) -> httpx.Response:
        from app.analysis.diff import parse_patch

        parts = path.split("/")  # /repos/{o}/{r}/pulls/{n}/reviews[/{id}[/comments]]
        full_name, number = f"{parts[2]}/{parts[3]}", int(parts[5])
        if not self._repo_access(token, full_name) or (full_name, number) not in self.pulls:
            return httpx.Response(404, json={"message": "Not Found"})
        reviews = self.reviews.setdefault((full_name, number), [])
        if request.method == "POST" and path.endswith("/reviews"):
            body = json.loads(request.content)
            comments = body.get("comments") or []
            if comments and self.reject_inline_comments:
                return httpx.Response(422, json={"message": "Line could not be resolved"})
            hunks = {
                f["filename"]: parse_patch(f.get("patch")).hunks
                for f in self.pull_files.get((full_name, number), [])
            }
            for c in comments:  # GitHub: every commented line must be inside one diff hunk
                start = c.get("start_line", c["line"])
                if not any(lo <= start and c["line"] <= hi for lo, hi in hunks.get(c["path"], [])):
                    return httpx.Response(
                        422,
                        json={
                            "message": "pull_request_review_thread.line must be part of the diff"
                        },
                    )
            review_id = 70_000 + sum(len(v) for v in self.reviews.values())
            stored = [{"id": review_id * 100 + i, **c} for i, c in enumerate(comments)]
            created: dict[str, Any] = {
                "id": review_id,
                "body": body["body"],
                "commit_id": body["commit_id"],
                "event": body["event"],
                "comments": stored,
                "html_url": f"{WEB}/{full_name}/pull/{number}#pullrequestreview-{review_id}",
            }
            reviews.append(created)
            return httpx.Response(200, json={k: v for k, v in created.items() if k != "comments"})
        review_id = int(parts[7])
        review = next((r for r in reviews if r["id"] == review_id), None)
        if review is None:
            return httpx.Response(404, json={"message": "Not Found"})
        if request.method == "PUT":
            review["body"] = json.loads(request.content)["body"]
            return httpx.Response(200, json={"id": review_id, "body": review["body"]})
        if path.endswith("/comments"):
            return httpx.Response(200, json=review["comments"])
        return httpx.Response(404, json={"message": "Not Found"})

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if form.get("grant_type") == "refresh_token":
            token = self.refresh_tokens.get(form.get("refresh_token", ""))
        else:
            token = self.codes.pop(form.get("code", ""), None)
        if token is None:
            return httpx.Response(200, json={"error": "bad_verification_code"})
        body: dict[str, Any] = {"access_token": token, "token_type": "bearer"}
        if self.expiring_tokens:
            body |= {
                "expires_in": 28800,
                "refresh_token": f"ghr_{token}",
                "refresh_token_expires_in": 15897600,
            }
        return httpx.Response(
            200, content=json.dumps(body), headers={"content-type": "application/json"}
        )
