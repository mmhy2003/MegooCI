"""Provider adapters for Git integration (PRD §6.16).

Each adapter encapsulates three concerns:
- `verify_signature(headers, body, secret)` - HMAC / shared-secret check on the
  raw request body. Uses `hmac.compare_digest` for constant-time comparison.
- `parse_push_event(headers, payload)` - extract the normalized event metadata
  (event_type, branch, commit_sha, author) we persist on WebhookDelivery and
  use to match pipelines. Non-push events (ping, pull_request, merge request,
  tag push) are parsed but only pushes trigger builds.
- `test_credential(base_url, token)` - lightweight round-trip call to the
  provider to validate a stored PAT; used by the connection `/test` endpoint.

The generic adapter speaks our own HMAC-SHA256 protocol (`X-MegooCI-Signature:
sha256=<hex>`) and uses `git ls-remote` to validate generic-Git credentials.
"""

from __future__ import annotations

import asyncio
import hmac
import time
import uuid
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping

import httpx


@dataclass
class ParsedEvent:
    event_type: str | None
    branch: str | None
    commit_sha: str | None
    author: str | None
    delivery_id: str
    # Whether this event should enqueue a build. Only verified push events for
    # a concrete branch do.
    should_trigger_build: bool


@dataclass
class ValidationResult:
    ok: bool
    status: str           # "ok" | "failed"
    detail: str
    http_status: int | None = None
    latency_ms: int | None = None


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def _lower_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Returns a case-insensitive header map. FastAPI's `Request.headers` is
    already case-insensitive, but when called from tests we accept a plain
    dict so normalize to lowercase keys."""
    return {k.lower(): v for k, v in headers.items()}


def _branch_from_ref(ref: str | None) -> str | None:
    """Extract a branch name from a Git ref. Examples:
    - `refs/heads/main` -> `main`
    - `refs/tags/v1.0`  -> `None` (not a branch)
    """
    if not ref:
        return None
    if ref.startswith("refs/heads/"):
        return ref[len("refs/heads/"):]
    return None


def _hex_hmac(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, sha256).hexdigest()


# ----------------------------------------------------------------------------
# GitHub
# ----------------------------------------------------------------------------
class GitHubAdapter:
    provider_type = "github"

    @staticmethod
    def verify_signature(
        headers: Mapping[str, str], body: bytes, secret: str
    ) -> bool:
        h = _lower_headers(headers)
        sig = h.get("x-hub-signature-256", "")
        if not sig.startswith("sha256="):
            return False
        expected = "sha256=" + _hex_hmac(secret, body)
        return hmac.compare_digest(sig, expected)

    @staticmethod
    def parse_push_event(
        headers: Mapping[str, str], payload: dict[str, Any]
    ) -> ParsedEvent:
        h = _lower_headers(headers)
        event_type = h.get("x-github-event", "unknown")
        delivery_id = h.get("x-github-delivery") or str(uuid.uuid4())

        branch: str | None = None
        commit_sha: str | None = None
        author: str | None = None
        should_trigger = False

        if event_type == "push":
            branch = _branch_from_ref(payload.get("ref"))
            commit_sha = payload.get("after") or payload.get("head_commit", {}).get(
                "id"
            )
            pusher = payload.get("pusher") or {}
            sender = payload.get("sender") or {}
            author = pusher.get("name") or sender.get("login")
            should_trigger = bool(branch and commit_sha)
        elif event_type == "pull_request":
            pr = payload.get("pull_request") or {}
            head = pr.get("head") or {}
            branch = head.get("ref")
            commit_sha = head.get("sha")
            author = (payload.get("sender") or {}).get("login")

        return ParsedEvent(
            event_type=event_type,
            branch=branch,
            commit_sha=commit_sha,
            author=author,
            delivery_id=delivery_id,
            should_trigger_build=should_trigger,
        )

    @staticmethod
    async def test_credential(
        base_url: str | None, token: str
    ) -> ValidationResult:
        url = (base_url or "https://api.github.com").rstrip("/") + "/user"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "MegooCI",
        }
        return await _http_test(url, headers)


# ----------------------------------------------------------------------------
# GitLab
# ----------------------------------------------------------------------------
class GitLabAdapter:
    provider_type = "gitlab"

    @staticmethod
    def verify_signature(
        headers: Mapping[str, str], body: bytes, secret: str
    ) -> bool:
        # GitLab uses a shared-secret header, not an HMAC.
        h = _lower_headers(headers)
        received = h.get("x-gitlab-token", "")
        return hmac.compare_digest(received, secret)

    @staticmethod
    def parse_push_event(
        headers: Mapping[str, str], payload: dict[str, Any]
    ) -> ParsedEvent:
        h = _lower_headers(headers)
        event_type = h.get("x-gitlab-event", "unknown")
        delivery_id = h.get("x-gitlab-event-uuid") or str(uuid.uuid4())

        branch: str | None = None
        commit_sha: str | None = None
        author: str | None = None
        should_trigger = False

        if event_type in ("Push Hook", "Tag Push Hook"):
            branch = _branch_from_ref(payload.get("ref"))
            commit_sha = payload.get("after") or payload.get("checkout_sha")
            author = payload.get("user_name") or payload.get("user_username")
            should_trigger = bool(branch and commit_sha) and event_type == "Push Hook"
        elif event_type == "Merge Request Hook":
            attrs = payload.get("object_attributes") or {}
            branch = attrs.get("source_branch")
            commit_sha = (attrs.get("last_commit") or {}).get("id")
            author = (payload.get("user") or {}).get("name")

        return ParsedEvent(
            event_type=event_type,
            branch=branch,
            commit_sha=commit_sha,
            author=author,
            delivery_id=delivery_id,
            should_trigger_build=should_trigger,
        )

    @staticmethod
    async def test_credential(
        base_url: str | None, token: str
    ) -> ValidationResult:
        url = (base_url or "https://gitlab.com").rstrip("/") + "/api/v4/user"
        headers = {
            "PRIVATE-TOKEN": token,
            "User-Agent": "MegooCI",
        }
        return await _http_test(url, headers)


# ----------------------------------------------------------------------------
# Generic Git
# ----------------------------------------------------------------------------
class GenericGitAdapter:
    provider_type = "generic"

    @staticmethod
    def verify_signature(
        headers: Mapping[str, str], body: bytes, secret: str
    ) -> bool:
        h = _lower_headers(headers)
        sig = h.get("x-megooci-signature", "")
        if not sig.startswith("sha256="):
            return False
        expected = "sha256=" + _hex_hmac(secret, body)
        return hmac.compare_digest(sig, expected)

    @staticmethod
    def parse_push_event(
        headers: Mapping[str, str], payload: dict[str, Any]
    ) -> ParsedEvent:
        h = _lower_headers(headers)
        event_type = h.get("x-megooci-event", "push")
        delivery_id = h.get("x-megooci-delivery") or str(uuid.uuid4())

        # We accept a small, GitHub-lite payload shape for generic pushes:
        # { "ref": "refs/heads/main", "after": "<sha>", "pusher": { "name" } }
        branch = _branch_from_ref(payload.get("ref")) or payload.get("branch")
        commit_sha = payload.get("after") or payload.get("commit_sha")
        author = None
        pusher = payload.get("pusher")
        if isinstance(pusher, dict):
            author = pusher.get("name")
        elif isinstance(pusher, str):
            author = pusher

        should_trigger = event_type == "push" and bool(branch and commit_sha)

        return ParsedEvent(
            event_type=event_type,
            branch=branch,
            commit_sha=commit_sha,
            author=author,
            delivery_id=delivery_id,
            should_trigger_build=should_trigger,
        )

    @staticmethod
    async def test_credential(
        base_url: str | None, token: str
    ) -> ValidationResult:
        """For generic Git, `base_url` is expected to be a repository URL
        (https://host/owner/repo.git). We shell out to `git ls-remote` with
        HTTP basic auth embedded in the URL.
        """
        if not base_url:
            return ValidationResult(
                ok=False,
                status="failed",
                detail="base_url is required for generic Git connection tests",
            )

        # Inject token as HTTP basic auth user. We accept both "<token>" and
        # "<user>:<token>" in the credential field.
        if ":" in token:
            authed_url = base_url.replace("://", f"://{token}@", 1)
        else:
            authed_url = base_url.replace("://", f"://x-access-token:{token}@", 1)

        started = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "ls-remote",
                "--heads",
                authed_url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=10
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return ValidationResult(
                    ok=False,
                    status="failed",
                    detail="git ls-remote timed out after 10s",
                    latency_ms=int((time.monotonic() - started) * 1000),
                )

            latency_ms = int((time.monotonic() - started) * 1000)
            if proc.returncode == 0:
                return ValidationResult(
                    ok=True,
                    status="ok",
                    detail=f"git ls-remote succeeded ({len(stdout.splitlines())} refs)",
                    latency_ms=latency_ms,
                )
            err = (stderr or b"").decode(errors="replace").strip()[:500] or (
                f"git exit {proc.returncode}"
            )
            return ValidationResult(
                ok=False,
                status="failed",
                detail=err,
                latency_ms=latency_ms,
            )
        except FileNotFoundError:
            return ValidationResult(
                ok=False,
                status="failed",
                detail="git binary not found on controller",
            )
        except Exception as exc:  # pragma: no cover
            return ValidationResult(
                ok=False,
                status="failed",
                detail=f"ls-remote failed: {exc}",
            )


# ----------------------------------------------------------------------------
# Shared HTTP tester for GitHub/GitLab
# ----------------------------------------------------------------------------
async def _http_test(url: str, headers: dict[str, str]) -> ValidationResult:
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
    except httpx.TimeoutException:
        return ValidationResult(
            ok=False,
            status="failed",
            detail=f"Request to {url} timed out",
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    except httpx.HTTPError as exc:
        return ValidationResult(
            ok=False,
            status="failed",
            detail=f"HTTP error: {exc}",
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    latency_ms = int((time.monotonic() - started) * 1000)
    if 200 <= response.status_code < 300:
        try:
            data = response.json()
            login = (
                data.get("login")
                or data.get("username")
                or data.get("name")
                or "authenticated"
            )
        except Exception:
            login = "authenticated"
        return ValidationResult(
            ok=True,
            status="ok",
            detail=f"Authenticated as {login}",
            http_status=response.status_code,
            latency_ms=latency_ms,
        )

    return ValidationResult(
        ok=False,
        status="failed",
        detail=f"Provider returned HTTP {response.status_code}",
        http_status=response.status_code,
        latency_ms=latency_ms,
    )


# ----------------------------------------------------------------------------
# Dispatcher
# ----------------------------------------------------------------------------
_ADAPTERS: dict[str, type] = {
    "github": GitHubAdapter,
    "gitlab": GitLabAdapter,
    "generic": GenericGitAdapter,
}


def get_adapter(provider_type: str):
    """Return the adapter class for a provider type. Raises ValueError on
    unknown provider so callers can translate to an HTTP error."""
    adapter = _ADAPTERS.get(provider_type.lower())
    if adapter is None:
        raise ValueError(f"Unknown provider_type: {provider_type}")
    return adapter
