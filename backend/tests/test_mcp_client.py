"""ApiClient calls the REST app in-process; format_api_error renders failures."""
import pytest
from fastapi import FastAPI, Header, HTTPException, Response

from app.mcp.client import INTERNAL_ERROR_MESSAGE, ApiClient, ApiError, format_api_error


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/v1/echo")
    async def echo(authorization: str = Header(None), a: str | None = None, b: str | None = None):
        return {"authorization": authorization, "a": a, "b": b}

    @app.post("/api/v1/items")
    async def create(body: dict):
        return {"got": body}

    @app.delete("/api/v1/items/1", status_code=204)
    async def delete():
        return Response(status_code=204)

    @app.get("/api/v1/forbidden")
    async def forbidden():
        raise HTTPException(status_code=403, detail="Permission 'builds.manage' required")

    @app.get("/api/v1/crash")
    async def crash():
        raise RuntimeError("secret internals")

    return app


async def test_forwards_the_callers_token():
    body = await ApiClient(_app(), "megci_pat_abc").get("/echo")
    assert body["authorization"] == "Bearer megci_pat_abc"


async def test_none_params_are_dropped():
    body = await ApiClient(_app(), "t").get("/echo", params={"a": "1", "b": None})
    assert body["a"] == "1" and body["b"] is None


async def test_json_body_is_sent():
    body = await ApiClient(_app(), "t").post("/items", json_body={"name": "x"})
    assert body == {"got": {"name": "x"}}


async def test_no_content_returns_none_and_records_status():
    api = ApiClient(_app(), "t")
    assert await api.delete("/items/1") is None
    assert api.last_status == 204


async def test_error_status_raises_api_error_with_detail():
    api = ApiClient(_app(), "t")
    with pytest.raises(ApiError) as exc:
        await api.get("/forbidden")
    assert exc.value.status_code == 403
    assert exc.value.detail == "Permission 'builds.manage' required"
    assert api.last_status == 403


async def test_unhandled_app_exception_propagates():
    """The server layer turns this into a generic tool error and logs it."""
    with pytest.raises(RuntimeError):
        await ApiClient(_app(), "t").get("/crash")


def test_format_string_detail_is_passed_through():
    assert format_api_error(ApiError(404, "Pipeline not found")) == "Pipeline not found"


def test_format_pipeline_validation_errors_lists_lines():
    detail = {
        "message": "Pipeline validation failed",
        "errors": [
            {"message": "YAML syntax error", "line": 4, "column": 3, "severity": "error"},
            {"message": "stages is required", "line": None, "column": None},
        ],
    }
    assert format_api_error(ApiError(400, detail)) == (
        "Pipeline validation failed\n"
        "  line 4, column 3: YAML syntax error\n"
        "  stages is required"
    )


def test_format_request_validation_list():
    detail = [{"loc": ["body", "name"], "msg": "Field required", "type": "missing"}]
    assert format_api_error(ApiError(422, detail)) == "Invalid request: name: Field required"


def test_format_5xx_hides_the_body():
    assert format_api_error(ApiError(500, "Traceback: secret internals")) == INTERNAL_ERROR_MESSAGE
    assert format_api_error(ApiError(502, {"x": 1})) == INTERNAL_ERROR_MESSAGE


def test_format_503_maintenance_detail_is_shown():
    assert "maintenance" in format_api_error(ApiError(503, "System is in maintenance mode."))


def test_format_unknown_shape_is_json():
    assert format_api_error(ApiError(400, {"code": 7})) == '{"code": 7}'
