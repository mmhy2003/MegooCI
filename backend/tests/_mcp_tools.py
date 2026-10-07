"""A fake REST client for testing MCP tool handlers without a server."""


class FakeApi:
    """Records REST calls and replays canned bodies keyed by (method, path)."""

    def __init__(self, responses=None, error=None):
        self.calls = []
        self._responses = responses or {}
        self._error = error

    async def _call(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if self._error is not None:
            raise self._error
        return self._responses.get((method, path))

    async def get(self, path, *, params=None):
        return await self._call("GET", path, params=params)

    async def post(self, path, *, json_body=None):
        return await self._call("POST", path, json_body=json_body)

    async def put(self, path, *, json_body=None):
        return await self._call("PUT", path, json_body=json_body)

    async def delete(self, path):
        return await self._call("DELETE", path)


async def run_tool(tool_name, api, /, **arguments):
    from app.mcp.tools import ALL_TOOLS

    spec = next(t for t in ALL_TOOLS if t.name == tool_name)
    return await spec.handler(api, spec.input_model.model_validate(arguments))
