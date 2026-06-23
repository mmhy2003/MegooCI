"""Shared test helper: create the two pipeline-concurrency partial unique
indexes on a SQLite test connection. SQLite supports partial indexes, so this
mirrors what migration 021 creates on Postgres."""


async def create_concurrency_indexes(conn) -> None:
    await conn.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_one_running_build_per_pipeline "
        "ON builds (pipeline_id) WHERE status = 'running'"
    )
    await conn.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_one_pending_build_per_pipeline "
        "ON builds (pipeline_id) WHERE status = 'pending'"
    )
