"""
Handler for ``ssh_exec`` steps — connect to a remote server over SSH and
execute commands.

Supports two authentication methods:
  1. **Private key** (recommended) — via ``private_key`` config.
  2. **Password** — via ``password`` config using ``sshpass``.

Falls back to the ``ssh`` CLI binary.  When using password auth, the
``sshpass`` utility must be installed in the execution environment.

YAML examples:

  # Key-based auth (recommended)
  - ssh_exec:
      host: deploy.example.com
      port: 22
      user: deploy
      private_key: ${{ secrets.SSH_DEPLOY_KEY }}
      commands:
        - "cd /opt/app && docker compose pull"
        - "docker compose up -d"
      env:
        APP_VERSION: "1.2.3"

  # Password-based auth
  - ssh_exec:
      host: deploy.example.com
      user: deploy
      password: ${{ secrets.SSH_PASSWORD }}
      commands:
        - "systemctl restart myapp"
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Any, AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from . import register
from .base import LogLine, StepActionHandler, StepContext, StepResult


class SSHExecHandler(StepActionHandler):

    async def execute(
        self,
        config: dict[str, Any],
        ctx: StepContext,
        db: AsyncSession,
    ) -> AsyncIterator[LogLine | StepResult]:
        host = config.get("host", "")
        port = config.get("port", 22)
        user = config.get("user", "")
        private_key = config.get("private_key", "")
        password = config.get("password", "")
        commands: list[str] = config.get("commands", [])
        remote_env: dict[str, str] = config.get("env", {})

        if not commands:
            yield StepResult(exit_code=0, status="success")
            return

        env_prefix = ""
        if remote_env:
            pairs = " ".join(f"{k}={_shell_quote(v)}" for k, v in remote_env.items())
            env_prefix = f"export {pairs} && "

        combined = " && ".join(commands)
        remote_script = f"{env_prefix}{combined}"

        auth_mode = "key" if private_key else ("password" if password else "none")
        yield LogLine(
            stream="system",
            content=f"Connecting to {user}@{host}:{port} (auth: {auth_mode})\n",
        )

        key_file = None
        password_file = None
        extra_env: dict[str, str] = {}
        try:
            # ── Build the ssh argument list ──
            ssh_args = ["ssh", "-o", "StrictHostKeyChecking=no"]

            if private_key:
                # Key-based auth — write key to a temp file.
                fd, key_file = tempfile.mkstemp(prefix="megooci_ssh_", suffix=".key")
                os.write(fd, private_key.encode())
                os.close(fd)
                os.chmod(key_file, 0o600)
                ssh_args += ["-o", "BatchMode=yes", "-i", key_file]
            elif password:
                # Password auth — use sshpass via environment variable.
                # SSHPASS env var avoids leaking the password in /proc/cmdline.
                fd, password_file = tempfile.mkstemp(prefix="megooci_sshpw_", suffix=".txt")
                os.write(fd, password.encode())
                os.close(fd)
                os.chmod(password_file, 0o600)
                extra_env["SSHPASS"] = password
                ssh_args = [
                    "sshpass", "-e",  # read password from $SSHPASS
                    "ssh",
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "PubkeyAuthentication=no",
                ]
            else:
                # No credentials — will rely on ssh-agent or fail.
                ssh_args += ["-o", "BatchMode=yes"]

            ssh_args += ["-p", str(port)]
            if user:
                ssh_args.append(f"{user}@{host}")
            else:
                ssh_args.append(host)
            ssh_args.append(remote_script)

            display_target = f"{user}@{host}" if user else host
            yield LogLine(stream="system", content=f"$ ssh {display_target} ...\n")

            try:
                process = await asyncio.create_subprocess_exec(
                    *ssh_args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    stdin=asyncio.subprocess.DEVNULL,
                    env={**os.environ, **extra_env} if extra_env else None,
                )
            except FileNotFoundError as fnf:
                missing = ssh_args[0]  # "ssh" or "sshpass"
                yield LogLine(
                    stream="stderr",
                    content=f"'{missing}' not found. "
                    + ("Install 'sshpass' for password auth.\n" if missing == "sshpass" else "Install an SSH client.\n"),
                )
                yield StepResult(exit_code=1, status="failed", error=f"{missing} not found")
                return
            except Exception as exc:
                yield LogLine(stream="stderr", content=f"SSH failed: {exc}\n")
                yield StepResult(exit_code=1, status="failed", error=str(exc))
                return

            lines: list[LogLine] = []

            async def _read(stream: asyncio.StreamReader, name: str) -> None:
                async for raw in stream:
                    lines.append(LogLine(stream=name, content=raw.decode(errors="replace")))

            t1 = asyncio.create_task(_read(process.stdout, "stdout"))  # type: ignore[arg-type]
            t2 = asyncio.create_task(_read(process.stderr, "stderr"))  # type: ignore[arg-type]

            while not t1.done() or not t2.done():
                await asyncio.sleep(0.05)
                while lines:
                    yield lines.pop(0)

            await asyncio.gather(t1, t2)
            while lines:
                yield lines.pop(0)

            await process.wait()
            exit_code = process.returncode or 0
            yield StepResult(
                exit_code=exit_code,
                status="success" if exit_code == 0 else "failed",
            )
        finally:
            for f in (key_file, password_file):
                if f:
                    try:
                        os.unlink(f)
                    except OSError:
                        pass

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not config.get("host"):
            errors.append("ssh_exec requires a 'host'")
        if not config.get("commands"):
            errors.append("ssh_exec requires at least one command in 'commands'")
        if not isinstance(config.get("commands", []), list):
            errors.append("ssh_exec 'commands' must be a list")
        return errors


def _shell_quote(s: str) -> str:
    """Minimal POSIX quoting for env var values passed over SSH."""
    if not s:
        return "''"
    return "'" + s.replace("'", "'\"'\"'") + "'"


register("ssh_exec", SSHExecHandler())

