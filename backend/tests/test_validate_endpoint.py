"""POST /pipelines/validate returns structured results for good and bad YAML."""

import pytest
from fastapi import HTTPException

GOOD = "name: demo\nstages:\n  - name: build\n    steps:\n      - run: echo hi\n"
BAD = "name: demo\nstages:\n  - name: build\n   steps:\n      - run: echo hi\n"  # bad indent


async def test_validate_endpoint_accepts_valid():
    from app.api.v1.pipelines import validate_pipeline_yaml
    from app.schemas.pipeline import PipelineValidateRequest

    resp = await validate_pipeline_yaml(
        PipelineValidateRequest(yaml_content=GOOD), _current_user=None
    )
    assert resp.valid is True
    assert resp.errors == []


async def test_validate_endpoint_reports_line_for_bad_syntax():
    from app.api.v1.pipelines import validate_pipeline_yaml
    from app.schemas.pipeline import PipelineValidateRequest

    resp = await validate_pipeline_yaml(
        PipelineValidateRequest(yaml_content=BAD), _current_user=None
    )
    assert resp.valid is False
    assert resp.errors
    assert resp.errors[0].line is not None
    assert "YAML syntax error" in resp.errors[0].message


async def test_validate_endpoint_rejects_oversized_body():
    from app.api.v1.pipelines import validate_pipeline_yaml
    from app.schemas.pipeline import PipelineValidateRequest

    huge = "a: 1\n" * 100_000  # > 256 KiB
    with pytest.raises(HTTPException) as exc_info:
        await validate_pipeline_yaml(
            PipelineValidateRequest(yaml_content=huge), _current_user=None
        )
    assert exc_info.value.status_code == 413
