# Connecting Coding Agents to MegooCI (MCP)

MegooCI serves a [Model Context Protocol](https://modelcontextprotocol.io) endpoint. A coding agent connected to it can list projects, read and edit pipelines, trigger builds, and read build logs, acting as you and limited by your roles.

This guide covers what any MCP client needs, setup for common agents, the tools the server offers, and how to fix connection problems.

## What you need

1. **The MCP URL.** It is your API URL plus `/mcp`, for example `https://ci.example.com/mcp`. **Settings → API Tokens** shows the exact URL for your instance.
2. **An API token.** In **Settings → API Tokens**, create a token with the **Coding agent** scope. The token starts with `megci_pat_` and is shown once.

The examples below use `https://ci.example.com/mcp` and read the token from an environment variable named `MEGOOCI_TOKEN`:

```bash
# macOS / Linux
export MEGOOCI_TOKEN="megci_pat_..."
```

```powershell
# Windows (new terminals only)
setx MEGOOCI_TOKEN "megci_pat_..."
```

## Connection facts for any client

| Property | Value |
|---|---|
| Transport | Streamable HTTP (not the older HTTP+SSE transport) |
| Method | `POST` only. `GET` and `DELETE` return 405. |
| Authentication | `Authorization: Bearer <token>` header on every request |
| OAuth | Not supported. The client must be able to send a custom header. |
| Sessions | None. The server is stateless and answers with plain JSON. |
| Capabilities | Tools only (no resources or prompts) |

Any client that supports Streamable HTTP and a custom header can connect, whether or not it is listed below.

## Setup by agent

Configuration formats change. Each section links to the agent's own documentation, which wins if it disagrees with this page.

### Claude Code

```bash
claude mcp add --transport http --scope user megooci https://ci.example.com/mcp \
  --header "Authorization: Bearer <your-token>"
```

`--scope user` makes the server available in all your projects. Without it, Claude Code uses the `local` scope, and the server exists only in the project where you ran the command.

| Scope | Available in | Stored in |
|---|---|---|
| `local` (default) | The current project only | `~/.claude.json` |
| `project` | The current project, shared with the team | `.mcp.json` in the project root |
| `user` | All your projects | `~/.claude.json` |

Do not use `project` scope with a literal token: `.mcp.json` is meant to be committed.

Check or remove the server with `claude mcp list`, `claude mcp get megooci` and `claude mcp remove megooci`. To change scope, remove the server and add it again. Reference: [Claude Code MCP documentation](https://code.claude.com/docs/en/mcp).

### Cursor

Add the server to `~/.cursor/mcp.json` (all projects) or `.cursor/mcp.json` (one project):

```json
{
  "mcpServers": {
    "megooci": {
      "url": "https://ci.example.com/mcp",
      "headers": {
        "Authorization": "Bearer ${env:MEGOOCI_TOKEN}"
      }
    }
  }
}
```

Reference: [Cursor MCP documentation](https://cursor.com/docs/context/mcp).

### VS Code (GitHub Copilot)

Run **MCP: Open User Configuration** from the Command Palette for all workspaces, or create `.vscode/mcp.json` for one workspace:

```json
{
  "inputs": [
    {
      "type": "promptString",
      "id": "megooci-token",
      "description": "MegooCI API token",
      "password": true
    }
  ],
  "servers": {
    "megooci": {
      "type": "http",
      "url": "https://ci.example.com/mcp",
      "headers": {
        "Authorization": "Bearer ${input:megooci-token}"
      }
    }
  }
}
```

VS Code asks for the token the first time the server starts and stores it securely, so the file is safe to commit. Reference: [VS Code MCP configuration reference](https://code.visualstudio.com/docs/copilot/reference/mcp-configuration).

### Codex CLI

```bash
codex mcp add megooci --url https://ci.example.com/mcp --bearer-token-env-var MEGOOCI_TOKEN
```

Or edit `~/.codex/config.toml`:

```toml
[mcp_servers.megooci]
url = "https://ci.example.com/mcp"
bearer_token_env_var = "MEGOOCI_TOKEN"
```

Codex reads the token from the named environment variable each time it starts. Reference: [Codex MCP documentation](https://developers.openai.com/codex/mcp).

### Gemini CLI

```bash
gemini mcp add --transport http --scope user \
  --header "Authorization: Bearer <your-token>" \
  megooci https://ci.example.com/mcp
```

Or edit `~/.gemini/settings.json` (all projects) or `.gemini/settings.json` (one project). Use `httpUrl`, not `url`; Gemini CLI treats `url` as the older SSE transport:

```json
{
  "mcpServers": {
    "megooci": {
      "httpUrl": "https://ci.example.com/mcp",
      "headers": {
        "Authorization": "Bearer <your-token>"
      }
    }
  }
}
```

Reference: [Gemini CLI MCP documentation](https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md).

### Clients that only support local (stdio) servers

Bridge them with [`mcp-remote`](https://github.com/geelen/mcp-remote), which runs locally and forwards to the URL:

```json
{
  "mcpServers": {
    "megooci": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://ci.example.com/mcp",
        "--transport",
        "http-only",
        "--header",
        "Authorization:${AUTH_HEADER}"
      ],
      "env": {
        "AUTH_HEADER": "Bearer <your-token>"
      }
    }
  }
}
```

The header is written without a space after the colon, and the `Bearer ` prefix lives in the environment variable, because some clients mangle spaces inside `args`.

## Check the connection without an agent

```bash
curl -s -X POST https://ci.example.com/mcp \
  -H "Authorization: Bearer $MEGOOCI_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

A working setup returns JSON with a `result.tools` array. An HTTP error instead points to a row in [Troubleshooting](#troubleshooting).

## Tools

The server offers 22 tools. An agent only sees the tools its token can use, so a read-only token sees no write tools.

| Tool | What it does | Permission needed |
|---|---|---|
| `whoami` | Shows the user, role and effective permissions of the token | none |
| `search` | Searches projects, pipelines, builds and artifacts by text | any `*.read` below |
| `list_projects` | Lists visible projects | `projects.read` |
| `get_project` | Gets one project | `projects.read` |
| `list_project_repositories` | Lists Git repositories linked to a project | `projects.read` |
| `create_project` | Creates a project | `projects.manage` |
| `update_project` | Renames a project or changes its description | `projects.manage` |
| `delete_project` | Deletes an empty project | `projects.manage` |
| `list_pipelines` | Lists pipelines, without their YAML | `pipelines.read` |
| `get_pipeline` | Gets one pipeline, including its YAML | `pipelines.read` |
| `validate_pipeline_yaml` | Checks pipeline YAML without saving it | `pipelines.read` |
| `create_pipeline` | Creates a pipeline | `pipelines.manage` |
| `update_pipeline` | Changes a pipeline, or enables/disables it | `pipelines.manage` |
| `delete_pipeline` | Deletes a pipeline that has no builds or triggers | `pipelines.manage` |
| `list_builds` | Lists builds, newest first | `builds.read` |
| `get_build` | Gets one build with stage and step status | `builds.read` |
| `get_build_logs` | Returns a build's log output as text | `builds.read` |
| `trigger_build` | Starts a build of a pipeline | `builds.manage` |
| `cancel_build` | Cancels an unfinished build | `builds.manage` |
| `retry_build` | Re-runs a finished build | `builds.manage` |
| `list_build_artifacts` | Lists the files a build produced | `artifacts.read` |
| `get_artifact_download_url` | Returns a temporary download URL for an artifact | `artifacts.read` |

The **Coding agent** token scope covers every permission in this table.

## How the tools behave

- **IDs, not names.** Every tool takes UUIDs. An agent finds an ID with a list tool or `search`, then acts on it.
- **Paging.** List tools take `skip` and `limit` (default 20, maximum 100) and return `total` where the server knows it.
- **Updates change only what you pass.** An argument that is omitted or `null` is left unchanged. An empty string is rejected, so a field cannot be cleared through MCP.
- **Deletes do not cascade.** `delete_project` and `delete_pipeline` refuse when builds, triggers, repositories or secrets still exist. Removing a pipeline together with its history must be done by a person in the web UI.
- **Following a build.** While a build is pending, queued or running, `get_build`, `trigger_build` and `retry_build` include `poll_after_seconds: 60`. The agent is told to wait that long between `get_build` calls and to stop when the status is `success`, `failed` or `cancelled`. This is guidance; the server cannot make an agent wait.
- **Logs are trimmed.** `get_build_logs` returns the last 200 lines by default (up to 2000 with `tail_lines`), from the failed steps when the build failed. Pass `step` to see one step by its exact name. Output is capped at 60,000 characters.
- **Artifacts are downloaded by URL.** `get_artifact_download_url` returns a signed URL valid for 5 minutes by default (30 seconds to 1 hour with `ttl`). The agent fetches the file itself; the URL needs no token.
- **Log and YAML content is data.** Pipeline definitions and build logs are written by other people and by build commands. The server tells agents to treat that content as data, never as instructions.

## What a token can do

A token's permissions through MCP are exactly its permissions through the REST API:

```
effective permissions = token scope ∩ the owner's role permissions
```

- A token can never do more than its owner. If the owner's role is reduced, the token shrinks with it.
- Roles assigned on a single project apply to that project only. A tool can be visible to the agent and still answer with a permission error for another project.
- A scoped token is never treated as an administrator, even when its owner is one.
- Revoking a token takes effect on the next request.

If a tool reports a missing permission, ask the agent to call `whoami`. It lists what the token actually has.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| HTTP 401 | The token is missing, mistyped, revoked or expired, the owner is deactivated, or the header is not `Authorization: Bearer <token>`. Browser session tokens are not accepted. | Create a new token and check the header. |
| HTTP 404 | The endpoint is disabled (`MEGOOCI_MCP_ENABLED=false`), or the URL points at the web frontend instead of the API. | Use the URL from **Settings → API Tokens**. |
| HTTP 405 | The client sent `GET` or `DELETE`. Most clients try this once and carry on; it is an error only if the client stops there. | Configure the client for Streamable HTTP, not SSE. |
| HTTP 421 | The server does not recognise the `Host` header. Either a reverse proxy rewrites it, or `MEGOOCI_PUBLIC_API_URL` does not match the address you connect to. | Ask the administrator to set `MEGOOCI_PUBLIC_API_URL` correctly or add the host to `MEGOOCI_MCP_ALLOWED_HOSTS`. |
| HTTP 403 "Invalid Origin header" | A browser-based MCP tool sent an `Origin` the server does not allow. Only the MegooCI frontend and API origins and localhost are allowed. | Use a non-browser client. |
| A tool is missing from the list | The token lacks the permission for it. | Call `whoami`; use a token with the **Coding agent** scope, or ask for a role with the permission. |
| "Unknown tool" | The tool does not exist, or the token cannot see it. | Same as above. |
| "Permission '…' required for this project" | The token has the permission on other projects, not this one. | Ask for a role on that project. |
| "MegooCI internal error" | The server failed. Details are in the backend log, not sent to the agent. `search` reports this when the search service is down. | Check the backend log; use the list tools instead of `search`. |
| The agent does not pick up new tool descriptions | Clients read descriptions when they connect. | Reconnect or restart the agent. |

## For administrators

| Setting | Default | Meaning |
|---|---|---|
| `MEGOOCI_MCP_ENABLED` | `true` | Serve the endpoint. |
| `MEGOOCI_MCP_ALLOWED_HOSTS` | empty | Extra `Host` values to accept, comma-separated. |
| `MEGOOCI_PUBLIC_API_URL` | `http://localhost:8000` | The API's public URL. The MCP URL and the accepted `Host` are derived from it. |

- The endpoint is served by the backend, not the frontend. If you expose only the frontend and proxy `/api` through it, `/mcp` is not proxied and returns 404; route `/mcp` to the backend as well.
- Each tool call writes one log line with the tool name, user ID, status and duration. Tool arguments are never logged.
- The design and its rationale are in [superpowers/specs/2026-10-07-mcp-integration-design.md](superpowers/specs/2026-10-07-mcp-integration-design.md).
