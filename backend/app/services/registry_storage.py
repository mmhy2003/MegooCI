"""Content-addressed blob and manifest storage for the embedded OCI registry.

Blobs are stored under ``<root>/blobs/sha256/<digest>`` and manifests under
``<root>/manifests/<name>/<reference>``.  Uploads use a two-phase model: the
client streams chunks into a temporary upload directory, then the blob is
finalized (moved) into content-addressed storage on ``PUT``.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from pathlib import Path

from app.config import get_settings


def _registry_root() -> Path:
    settings = get_settings()
    root = Path(settings.MEGOOCI_REGISTRY_STORAGE_PATH)
    root.mkdir(parents=True, exist_ok=True)
    return root


# ---------------------------------------------------------------------------
# Blobs
# ---------------------------------------------------------------------------

def blob_path(digest: str) -> Path:
    algo, hex_hash = digest.split(":", 1)
    return _registry_root() / "blobs" / algo / hex_hash[:2] / hex_hash


def blob_exists(digest: str) -> bool:
    return blob_path(digest).is_file()


def blob_size(digest: str) -> int:
    p = blob_path(digest)
    return p.stat().st_size if p.is_file() else 0


def read_blob(digest: str) -> bytes | None:
    p = blob_path(digest)
    return p.read_bytes() if p.is_file() else None


def store_blob(digest: str, data: bytes) -> Path:
    p = blob_path(digest)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.is_file():
        p.write_bytes(data)
    return p


def delete_blob(digest: str) -> bool:
    p = blob_path(digest)
    if p.is_file():
        p.unlink()
        return True
    return False


# ---------------------------------------------------------------------------
# Chunked uploads
# ---------------------------------------------------------------------------

def _uploads_dir() -> Path:
    d = _registry_root() / "_uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_upload() -> str:
    upload_id = uuid.uuid4().hex
    upload_path = _uploads_dir() / upload_id
    upload_path.touch()
    return upload_id


def append_upload(upload_id: str, data: bytes) -> int:
    upload_path = _uploads_dir() / upload_id
    with open(upload_path, "ab") as f:
        f.write(data)
    return upload_path.stat().st_size


def upload_exists(upload_id: str) -> bool:
    return (_uploads_dir() / upload_id).is_file()


def upload_offset(upload_id: str) -> int:
    p = _uploads_dir() / upload_id
    return p.stat().st_size if p.is_file() else 0


def finalize_upload(upload_id: str, expected_digest: str) -> bool:
    """Verify digest, move upload to content-addressed storage, clean up."""
    upload_path = _uploads_dir() / upload_id
    if not upload_path.is_file():
        return False

    algo, expected_hash = expected_digest.split(":", 1)
    h = hashlib.new(algo)
    with open(upload_path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)

    if h.hexdigest() != expected_hash:
        upload_path.unlink(missing_ok=True)
        return False

    dest = blob_path(expected_digest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.is_file():
        shutil.move(str(upload_path), str(dest))
    else:
        upload_path.unlink(missing_ok=True)
    return True


def cancel_upload(upload_id: str) -> None:
    (_uploads_dir() / upload_id).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Manifests (stored both by tag and by digest under the repo namespace)
# ---------------------------------------------------------------------------

def _manifests_dir(repo_path: str) -> Path:
    d = _registry_root() / "manifests" / repo_path
    d.mkdir(parents=True, exist_ok=True)
    return d


def store_manifest(repo_path: str, reference: str, data: bytes, digest: str) -> Path:
    base = _manifests_dir(repo_path)
    by_tag = base / "tags" / reference
    by_tag.parent.mkdir(parents=True, exist_ok=True)
    by_tag.write_bytes(data)

    by_digest = base / "digests" / digest.replace(":", "-")
    by_digest.parent.mkdir(parents=True, exist_ok=True)
    by_digest.write_bytes(data)
    return by_tag


def read_manifest(repo_path: str, reference: str) -> bytes | None:
    base = _manifests_dir(repo_path)
    if reference.startswith("sha256:"):
        p = base / "digests" / reference.replace(":", "-")
    else:
        p = base / "tags" / reference
    return p.read_bytes() if p.is_file() else None


def delete_manifest(repo_path: str, reference: str) -> bool:
    base = _manifests_dir(repo_path)
    if reference.startswith("sha256:"):
        p = base / "digests" / reference.replace(":", "-")
    else:
        p = base / "tags" / reference
    if p.is_file():
        p.unlink()
        return True
    return False


def manifest_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Garbage collection helpers
# ---------------------------------------------------------------------------

def list_all_blobs() -> list[str]:
    """Return all blob digests in storage."""
    blobs_root = _registry_root() / "blobs" / "sha256"
    if not blobs_root.is_dir():
        return []
    result = []
    for prefix_dir in blobs_root.iterdir():
        if prefix_dir.is_dir():
            for blob_file in prefix_dir.iterdir():
                if blob_file.is_file():
                    result.append(f"sha256:{blob_file.name}")
    return result


def total_blob_size() -> int:
    """Sum of all blob sizes on disk."""
    total = 0
    blobs_root = _registry_root() / "blobs" / "sha256"
    if not blobs_root.is_dir():
        return 0
    for prefix_dir in blobs_root.iterdir():
        if prefix_dir.is_dir():
            for blob_file in prefix_dir.iterdir():
                if blob_file.is_file():
                    total += blob_file.stat().st_size
    return total
