"""Display labels for pipeline steps that have no ``name``.

A label is built from the step's settings as written in the YAML, before any
placeholder is resolved, so it can never contain a secret or env value.
"""

from __future__ import annotations

import re
from typing import Any

MAX_LABEL_LENGTH = 60

# ${{ env.NAME }} / ${{ secrets.NAME }} → NAME; any other placeholder → its path.
_PLACEHOLDER = re.compile(r"\$\{\{\s*(?:(?:env|secrets)\.)?([^}]*?)\s*\}\}")


def _text(value: Any) -> str:
    if value is None:
        return ""
    return _PLACEHOLDER.sub(r"\1", str(value)).strip()


def _first_line(value: Any) -> str:
    for line in _text(value).splitlines():
        if line.strip():
            return line.strip()
    return ""


def _items(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_text(v) for v in value if _text(v)]
    return [_text(value)] if _text(value) else []


def _first_of(values: list[str], short=lambda v: v) -> str:
    if not values:
        return ""
    more = f" +{len(values) - 1} more" if len(values) > 1 else ""
    return short(values[0]) + more


def _image(tag: str) -> str:
    """registry.example.com/team/backend:latest → backend:latest"""
    return tag.rsplit("/", 1)[-1]


def _repo(url: str) -> str:
    """https://git.example.com/team/app.git → app"""
    name = url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return name[:-4] if name.endswith(".git") else name


def _with(prefix: str, detail: str, fallback: str) -> str:
    return f"{prefix} {detail}" if detail else fallback


def _describe(step_type: str, config: dict[str, Any]) -> str:
    get = config.get
    if step_type == "run":
        return _first_line(get("command")) or "run"
    if step_type == "git_clone":
        branch = _text(get("branch"))
        label = _with("Clone", _repo(_text(get("repo"))), "Git clone")
        return f"{label} ({branch})" if branch else label
    if step_type in ("git_pull", "git_push"):
        branch = _text(get("branch"))
        label = "Git pull" if step_type == "git_pull" else "Git push"
        return f"{label} ({branch})" if branch else label
    if step_type == "docker_build":
        return _with("Build", _first_of(_items(get("tags")), _image), "Docker build")
    if step_type == "docker_push":
        tags = _items(get("tags")) or _items(get("image"))
        return _with("Push", _first_of(tags, _image), "Docker push")
    if step_type == "docker_login":
        return _with("Log in to", _text(get("registry")), "Docker login")
    if step_type == "ssh_exec":
        host, user = _text(get("host")), _text(get("user"))
        return _with("SSH", f"{user}@{host}" if user and host else host, "SSH")
    if step_type == "kube_apply":
        return _with("Apply", _first_of(_items(get("manifests"))), "Kubernetes apply")
    if step_type == "http_request":
        method = (_text(get("method")) or "POST").upper()
        return f"{method} {_text(get('url'))}".strip()
    if step_type == "write_file":
        return _with("Write", _text(get("path")), "Write file")
    if step_type == "copy_files":
        source, destination = _text(get("source")), _text(get("destination"))
        return f"Copy {source} to {destination}" if source and destination else "Copy files"
    if step_type == "delete_files":
        paths = _items(get("path")) or _items(get("paths"))
        return _with("Delete", _first_of(paths), "Delete files")
    if step_type == "notify":
        return _with("Notify", _text(get("channel")), "Notify")
    if step_type == "trigger_pipeline":
        return _with("Trigger", _text(get("pipeline")), "Trigger pipeline")
    if step_type == "wait_input":
        return "Wait for approval"
    if step_type == "wait_webhook":
        return "Wait for webhook"
    if step_type == "ai_agent":
        return _with("AI agent:", _first_line(get("prompt")), "AI agent")
    return step_type


def step_label(step_type: str, config: dict[str, Any]) -> str:
    """A short label saying what an unnamed step does."""
    label = _describe(step_type, config)
    if len(label) > MAX_LABEL_LENGTH:
        label = label[: MAX_LABEL_LENGTH - 1].rstrip() + "…"
    return label


def numbered(label: str, taken: set[str]) -> str:
    """``label``, or ``label (2)``, ``label (3)``… when it is already taken."""
    if label not in taken:
        return label
    n = 2
    while f"{label} ({n})" in taken:
        n += 1
    return f"{label} ({n})"
