"""Build and artifact tools map to the right REST calls."""
import pytest

from tests._mcp_tools import FakeApi, run_tool

LID = "22222222-2222-2222-2222-222222222222"
BID = "33333333-3333-3333-3333-333333333333"
AID = "44444444-4444-4444-4444-444444444444"


# ── builds ──────────────────────────────────────────────────────────────

BUILD = {
    "id": BID, "pipeline_id": LID, "number": 4, "status": "failed", "branch": "main",
    "commit_sha": "abc", "trigger_type": "manual", "started_at": "t1", "finished_at": "t2",
    "triggered_by": "u1", "params_json": {"env": "prod"}, "created_at": "t0", "updated_at": "t2",
}
BUILD_ROW = {k: BUILD[k] for k in (
    "id", "pipeline_id", "number", "status", "branch", "commit_sha",
    "trigger_type", "started_at", "finished_at",
)}


async def test_list_builds_returns_compact_rows():
    api = FakeApi({("GET", "/builds"): [BUILD]})
    assert await run_tool("list_builds", api, pipeline_id=LID, limit=5) == {"items": [BUILD_ROW]}
    assert api.calls == [("GET", "/builds", {"params": {"pipeline_id": LID, "skip": 0, "limit": 5}})]


async def test_get_build_keeps_step_status_and_drops_config():
    detail = {**BUILD, "stages": [{
        "id": "st1", "build_id": BID, "name": "test", "status": "failed", "sort_order": 0,
        "started_at": "a", "finished_at": "b",
        "steps": [{
            "id": "sp1", "stage_id": "st1", "name": "pytest", "step_type": "run",
            "command": "pytest -q", "config_json": {"env": {"TOKEN": "s3cret"}},
            "status": "failed", "exit_code": 1, "sort_order": 0,
            "started_at": "a", "finished_at": "b",
        }],
    }]}
    api = FakeApi({("GET", f"/builds/{BID}"): detail})
    result = await run_tool("get_build", api, build_id=BID)
    assert result["params_json"] == {"env": "prod"}
    assert result["stages"] == [{
        "id": "st1", "name": "test", "status": "failed", "started_at": "a", "finished_at": "b",
        "steps": [{"id": "sp1", "name": "pytest", "step_type": "run", "status": "failed",
                   "exit_code": 1, "started_at": "a", "finished_at": "b"}],
    }]
    assert "s3cret" not in str(result)


async def test_get_build_logs_reads_build_and_logs_then_renders():
    detail = {**BUILD, "stages": [{"name": "test", "steps": [
        {"id": "sp1", "name": "pytest", "status": "failed"},
    ]}]}
    api = FakeApi({
        ("GET", f"/builds/{BID}"): detail,
        ("GET", f"/builds/{BID}/logs"): [
            {"step_id": "sp1", "stage_name": "test", "step_name": "pytest", "content": "boom\n"},
        ],
    })
    text = await run_tool("get_build_logs", api, build_id=BID)
    assert isinstance(text, str)
    assert "Build #4 — status: failed" in text and "boom" in text
    assert [c[1] for c in api.calls] == [f"/builds/{BID}", f"/builds/{BID}/logs"]


async def test_get_build_logs_rejects_out_of_range_tail():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        await run_tool("get_build_logs", FakeApi(), build_id=BID, tail_lines=0)


async def test_trigger_build_posts_to_the_pipeline_trigger_route():
    api = FakeApi({("POST", f"/builds/{LID}/trigger"): BUILD})
    result = await run_tool("trigger_build", api, pipeline_id=LID, branch="dev", params={"env": "prod"})
    assert result == BUILD_ROW
    assert api.calls == [("POST", f"/builds/{LID}/trigger", {"json_body": {
        "branch": "dev", "commit_sha": None, "params": {"env": "prod"},
    }})]


async def test_cancel_and_retry_post_to_their_routes():
    api = FakeApi({("POST", f"/builds/{BID}/cancel"): BUILD, ("POST", f"/builds/{BID}/retry"): BUILD})
    assert await run_tool("cancel_build", api, build_id=BID) == BUILD_ROW
    assert await run_tool("retry_build", api, build_id=BID) == BUILD_ROW
    assert [c[1] for c in api.calls] == [f"/builds/{BID}/cancel", f"/builds/{BID}/retry"]


# ── artifacts ───────────────────────────────────────────────────────────

async def test_list_build_artifacts_returns_compact_rows():
    api = FakeApi({("GET", f"/builds/{BID}/artifacts"): [{
        "id": AID, "build_id": BID, "relative_path": "dist/app.zip", "size_bytes": 10,
        "checksum_sha256": "ff", "retention_until": None, "created_at": "t",
    }]})
    assert await run_tool("list_build_artifacts", api, build_id=BID) == [{
        "id": AID, "relative_path": "dist/app.zip", "size_bytes": 10,
        "checksum_sha256": "ff", "created_at": "t",
    }]


async def test_get_artifact_download_url_passes_ttl():
    api = FakeApi({("GET", f"/artifacts/{AID}/signed-url"): {"url": "http://x", "expires_in": 60}})
    assert await run_tool("get_artifact_download_url", api, artifact_id=AID, ttl=60) == {
        "url": "http://x", "expires_in": 60,
    }
    assert api.calls == [("GET", f"/artifacts/{AID}/signed-url", {"params": {"ttl": 60}})]
