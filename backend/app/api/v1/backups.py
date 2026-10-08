"""Configuration backup and restore — administrators only.

Every route requires a global administrator: a restore can replace every
account and secret on the server, so an admin role held for one project is
not enough. The work is done by ``app.services.backup.service``; this module
checks who is asking and turns the service's refusals into HTTP errors.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from pydantic import BaseModel

from app import database
from app.config import get_settings
from app.core.access import has_global_permission
from app.core.deps import get_current_active_user
from app.models.user import User
from app.services.backup import remote, service, store
from app.services.backup import settings as backup_settings
from app.services.backup.format import BackupFormatError, WrongPassphrase, read_header
from app.services.backup.schedule import InvalidSchedule, Schedule
from app.services.backup.settings import InvalidSettings, RemoteConfig

async def require_global_admin(user: User = Depends(get_current_active_user)) -> User:
    """The signed-in user, if they administer the whole server. A token's
    scopes apply, as everywhere."""
    if not has_global_permission(user, "admin"):
        raise HTTPException(status_code=403, detail="Administrator access required.")
    return user


router = APIRouter(dependencies=[Depends(require_global_admin)])


def _secret_key() -> str:
    return get_settings().MEGOOCI_SECRET_KEY


@contextmanager
def _errors() -> Iterator[None]:
    """Turn what the backup service raises into the HTTP error for it."""
    try:
        yield
    except store.InvalidBackupName:
        raise HTTPException(status_code=400, detail="That is not a backup file name.")
    except store.BackupNotFound:
        raise HTTPException(status_code=404, detail="Backup not found.")
    except store.Busy as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except WrongPassphrase as exc:
        # 422 tells the page to ask for the passphrase the backup was made with.
        raise HTTPException(status_code=422, detail=str(exc))
    except BackupFormatError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except service.Refused as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (InvalidSettings, InvalidSchedule) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except remote.RemoteError as exc:
        raise HTTPException(status_code=502, detail=f"The remote storage reported: {exc}")
    except service.RestoreFailed as exc:
        raise HTTPException(status_code=500, detail=str(exc))


class ScheduleBody(BaseModel):
    frequency: str = "off"
    time: str = "03:00"
    weekday: int = 0
    keep: int = 14


class RemoteBody(BaseModel):
    enabled: bool = False
    endpoint_url: str = ""
    region: str = ""
    bucket: str = ""
    prefix: str = ""
    access_key_id: str = ""
    # Write-only. Empty keeps the stored key.
    secret_access_key: str = ""

    def to_config(self) -> RemoteConfig:
        return RemoteConfig(**self.model_dump())


class SettingsBody(BaseModel):
    passphrase: str | None = None
    schedule: ScheduleBody | None = None
    remote: RemoteBody | None = None


class RestoreBody(BaseModel):
    confirm: str = ""
    passphrase: str | None = None


@router.get("")
async def list_backups() -> dict[str, Any]:
    """The backups on this server, and the backup settings and last run."""
    with _errors():
        return await service.overview(database.async_session, _secret_key())


@router.post("", status_code=201)
async def create_backup(admin: User = Depends(require_global_admin)) -> dict[str, Any]:
    with _errors():
        backup = await service.create_backup(
            database.async_session, _secret_key(), kind="manual", actor_id=admin.id
        )
    return {"name": backup.name}


@router.post("/upload", status_code=201)
async def upload_backup(
    file: UploadFile, admin: User = Depends(require_global_admin)
) -> dict[str, Any]:
    # One byte past the limit is enough to know the file is too large.
    data = await file.read(service.MAX_UPLOAD_BYTES + 1)
    with _errors():
        backup = service.store_upload(data)
    await service.record_event(database.async_session, "backup.upload", admin.id, {"name": backup.name})
    return {"name": backup.name}


@router.put("/settings")
async def update_settings(
    body: SettingsBody, admin: User = Depends(require_global_admin)
) -> dict[str, Any]:
    with _errors():
        await service.update_settings(
            database.async_session,
            _secret_key(),
            passphrase=body.passphrase,
            schedule=Schedule(**body.schedule.model_dump()) if body.schedule else None,
            remote_config=body.remote.to_config() if body.remote else None,
            actor_id=admin.id,
        )
        return await service.overview(database.async_session, _secret_key())


@router.post("/settings/test-remote")
async def check_remote_settings(body: RemoteBody) -> dict[str, Any]:
    """Try the given remote storage settings without saving them. An empty
    secret access key means the stored one."""
    config = body.to_config()
    if not config.secret_access_key:
        async with database.async_session() as db:
            stored = await backup_settings.get_remote(db, _secret_key())
        config = replace(config, secret_access_key=stored.secret_access_key)
    with _errors():
        # Tested as they would be used: turned on.
        await service.check_remote(replace(config, enabled=True))
    return {"ok": True}


@router.get("/{name}/restore-checks")
async def restore_checks(name: str) -> dict[str, Any]:
    """What a restore of this backup needs, and whether each holds now."""
    with _errors():
        header = read_header(store.read(name))
        checks = await service.restore_checks(database.async_session, header)
    return {"checks": checks, "ready": all(check["ok"] for check in checks)}


@router.get("/{name}/download")
async def download_backup(name: str, admin: User = Depends(require_global_admin)) -> Response:
    with _errors():
        data = store.read(name)
    await service.record_event(database.async_session, "backup.download", admin.id, {"name": name})
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("/{name}/restore")
async def restore_backup(
    name: str, body: RestoreBody, admin: User = Depends(require_global_admin)
) -> dict[str, Any]:
    with _errors():
        return await service.restore_backup(
            database.async_session,
            _secret_key(),
            name,
            acting_user_id=admin.id,
            confirm=body.confirm,
            passphrase=body.passphrase or None,
        )


@router.post("/{name}/upload-remote")
async def upload_remote(name: str) -> dict[str, Any]:
    """Try the remote copy of this backup again."""
    with _errors():
        return await service.retry_remote(database.async_session, _secret_key(), name)


@router.delete("/{name}")
async def delete_backup(name: str, admin: User = Depends(require_global_admin)) -> dict[str, Any]:
    with _errors():
        remote_error = await service.delete_backup(
            database.async_session, _secret_key(), name, actor_id=admin.id
        )
    return {"deleted": name, "remote_error": remote_error}
