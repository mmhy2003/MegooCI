"""
Handler for ``ssh_exec`` steps — connect to a remote server over SSH and
execute commands.

Requires ``asyncssh`` in the backend environment.  Falls back to the
``ssh`` CLI binary if asyncssh is unavailable.

YAML example:

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

        yield LogLine(stream="system", content=f"Connecting to {user}@{host}:{port}\n")

        key_file = None
        try:
            if private_key:
                fd, key_file = tempfile.mkstemp(prefix="megooci_ssh_", suffix=".key")
                os.write(fd, private_key.encode())
                os.close(fd)
                os.chmod(key_file, 0o600)

            args = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes"]
            if key_file:
                args += ["-i", key_file]
            args += ["-p", str(port)]
            if user:
                args.append(f"{user}@{host}")
            else:
                args.append(host)
            args.append(remote_script)

            yield LogLine(stream="system", content=f"$ ssh {user}@{host} ...\n")

            try:
                process = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    stdin=asyncio.subprocess.DEVNULL,
                )
            except FileNotFoundError:
                yield LogLine(stream="stderr", content="'ssh' client not found.\n")
                yield StepResult(exit_code=1, status="failed", error="ssh not found")
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
            if key_file:
                try:
                    os.unlink(key_file)
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
