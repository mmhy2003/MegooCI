"""Seeding helpers for failure-notification tests."""
import uuid

PIPELINE_YAML = (
    "name: deploy-staging\n"
    "notifications:\n"
    "  on_failure:\n"
    "    - deploy-alerts\n"
    "stages:\n"
    "  - name: deploy\n"
    "    steps:\n"
    "      - run: ./deploy.sh\n"
)


class FakeRedis:
    """Just enough of redis.asyncio for the executor."""

    def __init__(self):
        self.store = {}
        self.published = []

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, *args, **kwargs):
        self.store[key] = value

    async def delete(self, key):
        self.store.pop(key, None)

    async def publish(self, channel, message):
        self.published.append((channel, message))
        return 0

    async def aclose(self):
        pass


async def seed_channel(db, name, channel_type="slack", *, enabled=True, config=None):
    """Insert a notification channel and return its id."""
    from app.models.notification import NotificationChannel
    from app.models.user import User
    from app.services.notification_service import encrypt_channel_config

    defaults = {
        "slack": {"webhook_url": "https://hooks.slack.example/services/T0/B0/hook-secret"},
        "telegram": {"bot_token": "123456:bot-token-secret", "default_chat_id": "-100"},
        "email": {"smtp_host": "smtp.example.com", "smtp_port": 587,
                  "from_email": "ci@example.com"},
    }
    owner = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@e.com", name="admin")
    db.add(owner)
    await db.flush()
    channel = NotificationChannel(
        id=uuid.uuid4(),
        name=name,
        channel_type=channel_type,
        config_encrypted=encrypt_channel_config(config or defaults[channel_type]),
        enabled=enabled,
        created_by=owner.id,
    )
    db.add(channel)
    await db.flush()
    return channel.id


async def seed_build(
    db,
    *,
    yaml_content=PIPELINE_YAML,
    pipeline_name="deploy-staging",
    project_name="Inbox Staging",
    build_status="failed",
    step_status="failed",
    stage_name="deploy",
    step_name="apply manifests",
    branch="develop",
    commit_sha="3f2a9c1d5e6f7a8b",
):
    """Insert project → pipeline → build → one stage → one step. Returns ids."""
    from app.models.build import Build, Stage, Step
    from app.models.pipeline import Pipeline
    from app.models.project import Project
    from app.models.user import User

    owner = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@e.com", name="owner")
    db.add(owner)
    await db.flush()
    project = Project(id=uuid.uuid4(), name=project_name,
                      slug=f"p-{uuid.uuid4().hex[:8]}", created_by=owner.id)
    db.add(project)
    await db.flush()
    pipeline = Pipeline(id=uuid.uuid4(), name=pipeline_name, project_id=project.id,
                        created_by=owner.id, yaml_content=yaml_content)
    db.add(pipeline)
    await db.flush()
    build = Build(id=uuid.uuid4(), pipeline_id=pipeline.id, number=428, status=build_status,
                  trigger_type="webhook", branch=branch, commit_sha=commit_sha)
    db.add(build)
    await db.flush()
    stage = Stage(id=uuid.uuid4(), build_id=build.id, name=stage_name,
                  status=step_status, sort_order=0)
    db.add(stage)
    await db.flush()
    step = Step(id=uuid.uuid4(), stage_id=stage.id, name=step_name, step_type="run",
                status=step_status, sort_order=0)
    db.add(step)
    await db.flush()
    return {"project": project.id, "pipeline": pipeline.id, "build": build.id,
            "stage": stage.id, "step": step.id}


async def load_build(db, build_id):
    """The build with stages and steps loaded, as the executor holds it."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.models.build import Build, Stage

    return (await db.execute(
        select(Build).where(Build.id == build_id)
        .options(selectinload(Build.stages).selectinload(Stage.steps))
    )).scalar_one()


def builtins_for(build, *, pipeline_name="deploy-staging", project_name="Inbox Staging"):
    """Placeholder values shaped like build_executor._load_scope_context's."""
    return {
        "build": {"id": str(build.id), "number": str(build.number),
                  "branch": build.branch or "", "commit": build.commit_sha or "",
                  "status": "running"},
        "pipeline": {"id": str(build.pipeline_id), "name": pipeline_name},
        "project": {"name": project_name},
        "megooci": {"url": "https://api.example.com"},
    }
