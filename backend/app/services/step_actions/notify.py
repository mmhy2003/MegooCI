"""Notify step action handler.

Sends a notification through a configured channel (email, Slack, Telegram)
as a pipeline step. Always executed server-side — never dispatched to agents.

YAML syntax:
    - notify:
        channel: "deploy-alerts"
        message: |
          Build #${{ build.number }} finished with status: ${{ build.status }}
          Pipeline: ${{ pipeline.name }}
        subject: "Build Report"       # optional (used by email)
        recipient: "#deployments"     # optional override
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.build import Build
from app.models.notification import NotificationChannel
from app.models.pipeline import Pipeline
from app.services.notification_service import render_message, send_notification
from app.services.step_actions import register
from app.services.step_actions.base import (
    LogLine,
    StepActionHandler,
    StepContext,
    StepResult,
)


class NotifyHandler(StepActionHandler):
    async def execute(
        self,
        config: dict[str, Any],
        ctx: StepContext,
        db: AsyncSession,
    ) -> AsyncIterator[LogLine | StepResult]:
        channel_name = config.get("channel", "")
        message_template = config.get("message", "")
        subject_template = config.get("subject")
        recipient = config.get("recipient")

        if not channel_name:
            yield LogLine(stream="stderr", content="Error: 'channel' is required in notify step\n")
            yield StepResult(exit_code=1, status="failed", error="Missing 'channel'")
            return

        if not message_template:
            yield LogLine(stream="stderr", content="Error: 'message' is required in notify step\n")
            yield StepResult(exit_code=1, status="failed", error="Missing 'message'")
            return

        template_ctx = {
            "build.id": str(ctx.build_id),
            "build.branch": ctx.branch or "",
            "build.commit_sha": ctx.commit_sha or "",
            "step.name": ctx.step_name,
            "stage.name": ctx.stage_name,
        }

        build = await db.get(Build, ctx.build_id)
        if build:
            template_ctx["build.number"] = str(build.number)
            template_ctx["build.status"] = build.status
            pipeline = await db.get(Pipeline, build.pipeline_id)
            if pipeline:
                template_ctx["pipeline.name"] = pipeline.name

        message = render_message(message_template, template_ctx)
        subject = render_message(subject_template, template_ctx) if subject_template else None

        yield LogLine(
            stream="stdout",
            content=f"Sending notification via channel '{channel_name}'...\n",
        )

        result = await db.execute(
            select(NotificationChannel).where(NotificationChannel.name == channel_name)
        )
        channel = result.scalar_one_or_none()

        if channel is None:
            yield LogLine(
                stream="stderr",
                content=f"Error: Notification channel '{channel_name}' not found\n",
            )
            yield StepResult(
                exit_code=1,
                status="failed",
                error=f"Channel '{channel_name}' not found",
            )
            return

        if not channel.enabled:
            yield LogLine(
                stream="stderr",
                content=f"Error: Notification channel '{channel_name}' is disabled\n",
            )
            yield StepResult(
                exit_code=1,
                status="failed",
                error=f"Channel '{channel_name}' is disabled",
            )
            return

        try:
            delivery = await send_notification(
                db,
                channel.id,
                message,
                subject=subject,
                recipient=recipient,
                build_id=ctx.build_id,
                step_id=ctx.step_id,
            )

            if delivery.status == "sent":
                yield LogLine(
                    stream="stdout",
                    content=f"Notification sent successfully via {channel.channel_type} channel '{channel_name}'\n",
                )
                yield StepResult(exit_code=0, status="success")
            else:
                yield LogLine(
                    stream="stderr",
                    content=f"Notification delivery failed: {delivery.error}\n",
                )
                yield StepResult(
                    exit_code=1,
                    status="failed",
                    error=delivery.error or "Delivery failed",
                )

        except Exception as exc:
            yield LogLine(
                stream="stderr",
                content=f"Notification error: {exc}\n",
            )
            yield StepResult(exit_code=1, status="failed", error=str(exc))

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not config.get("channel"):
            errors.append("'channel' is required")
        if not config.get("message"):
            errors.append("'message' is required")
        return errors


register("notify", NotifyHandler())
