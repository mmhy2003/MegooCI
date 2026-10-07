"""In-process client for the REST API, bound to one caller's token.

Every MCP tool goes through here, so authentication, permission checks and
validation are the REST handlers' own — nothing is re-implemented.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from starlette.types import ASGIApp

API_PREFIX = "/api/v1"
INTERNAL_ERROR_MESSAGE = "MegooCI internal error"
# 5xx statuses whose detail is written for end users and safe to pass on.
_PASSTHROUGH_5XX = {503}


class ApiError(Exception):
    """A REST call answered with an error status."""

    def __init__(self, status_code: int, detail: Any) -> None:
        super().__init__(f"{status_code}: {detail!r}")
        self.status_code = status_code
        self.detail = detail


def _error_detail(response: httpx.Response) -> Any:
    try:
        body = response.json()
    except ValueError:
        return response.text
    if isinstance(body, dict) and "detail" in body:
        return body["detail"]
    return body


class ApiClient:
    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._token = token
        # Status of the most recent REST call, for the per-tool log line.
        self.last_status: int | None = None

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
    ) -> Any:
        query = {k: v for k, v in (params or {}).items() if v is not None}
        transport = httpx.ASGITransport(app=self._app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://megooci.internal"
        ) as http:
            response = await http.request(
                method,
                f"{API_PREFIX}{path}",
                params=query,
                json=json_body,
                headers={"Authorization": f"Bearer {self._token}"},
            )
        self.last_status = response.status_code
        if response.status_code >= 400:
            raise ApiError(response.status_code, _error_detail(response))
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return await self.request("GET", path, params=params)

    async def post(self, path: str, *, json_body: Any = None) -> Any:
        return await self.request("POST", path, json_body=json_body)

    async def put(self, path: str, *, json_body: Any = None) -> Any:
        return await self.request("PUT", path, json_body=json_body)

    async def delete(self, path: str) -> Any:
        return await self.request("DELETE", path)


def format_api_error(error: ApiError) -> str:
    """Render a REST error as the text an agent sees."""
    if error.status_code >= 500 and error.status_code not in _PASSTHROUGH_5XX:
        return INTERNAL_ERROR_MESSAGE

    detail = error.detail
    if isinstance(detail, str):
        return detail

    # Pipeline validation: {"message": ..., "errors": [{message, line, column}]}
    if isinstance(detail, dict) and isinstance(detail.get("errors"), list):
        lines = [str(detail.get("message") or "Request failed")]
        for item in detail["errors"]:
            if not isinstance(item, dict):
                lines.append(f"  {item}")
                continue
            where = ""
            if item.get("line") is not None:
                where = f"line {item['line']}"
                if item.get("column") is not None:
                    where += f", column {item['column']}"
                where += ": "
            lines.append(f"  {where}{item.get('message', '')}")
        return "\n".join(lines)

    # FastAPI request validation: [{"loc": [...], "msg": ...}]
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if isinstance(item, dict) and "msg" in item:
                loc = ".".join(str(p) for p in item.get("loc", []) if p != "body")
                parts.append(f"{loc}: {item['msg']}" if loc else str(item["msg"]))
            else:
                parts.append(str(item))
        return "Invalid request: " + "; ".join(parts)

    return json.dumps(detail, default=str)
