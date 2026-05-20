# megooci-agent

Self-hosted build agent for [MegooCI](https://github.com/megooci/megooci).

A single static Go binary that connects outbound to a MegooCI controller over
WebSocket and runs build steps on behalf of the controller. Works on Linux,
macOS, and Windows, on both amd64 and arm64.

The Docker image ships a batteries-included toolchain — Python 3, Node.js +
npm + pnpm, uv, build-essential, Docker CLI + BuildKit, Temurin JDK 21,
Maven, and the Dart SDK — so most pipelines run without a custom base image.

Implements the agent side of **PRD §6.3** (F-3.4). Phase 1 ships with the
local-shell executor only; Docker / SSH / Kubernetes executors will satisfy
the same `executor.Executor` interface in future releases.

## Quickstart

### 1. Register the agent in the MegooCI UI

Go to **Agents → Register agent**, fill in a name + labels, and copy the
one-shot registration token MegooCI shows you. The token is only displayed
once — if you lose it, rotate it from the agent detail page.

### 2. Run the agent

```bash
megooci-agent run \
  --controller https://megooci.example.com \
  --agent-id   01234567-89ab-cdef-0123-456789abcdef \
  --token      megci_agt_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

### 3. Run it in Docker

From the monorepo root:

```bash
# One-off: build the image
make agent-image

# Register the agent in the UI, then:
make agent-up ID=<uuid> TOKEN=<megci_agt_...>

# Tail logs / stop
make agent-logs
make agent-down
```

Or directly with `docker run` — token + ID are CLI flags, never env vars:

```bash
docker run -d --restart=unless-stopped \
  --network megooci_default \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --group-add $(stat -c '%g' /var/run/docker.sock) \
  megooci/agent:latest \
  run \
    --controller http://backend:8000 \
    --agent-id   01234567-89ab-cdef-0123-456789abcdef \
    --token      megci_agt_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

Equivalent environment variables (`MEGOOCI_CONTROLLER_URL`,
`MEGOOCI_AGENT_ID`, `MEGOOCI_AGENT_TOKEN`, `MEGOOCI_AGENT_CAPACITY`,
`MEGOOCI_AGENT_LOG_LEVEL`) are still honored if you prefer to keep the
token out of your shell history — pass them with `-e` to `docker run` or
`export` them before running the binary directly.

For Docker-in-Docker pipelines, mount `/var/run/docker.sock` into the
container and pass `--group-add $(stat -c '%g' /var/run/docker.sock)` so
the agent process can access the socket (the GID varies across hosts).

## CLI reference

| Flag | Env | Default | Description |
| --- | --- | --- | --- |
| `--controller` | `MEGOOCI_CONTROLLER_URL` | — | Base URL of the controller (`https://...`). |
| `--agent-id` | `MEGOOCI_AGENT_ID` | — | UUID shown in the UI after registration. |
| `--token` | `MEGOOCI_AGENT_TOKEN` | — | Persistent bearer token. |
| `--capacity` | `MEGOOCI_AGENT_CAPACITY` | `1` | Max concurrent steps. |
| `--workdir` | `MEGOOCI_AGENT_WORKDIR` | auto | Cwd for subprocesses (auto = per-build shared temp dir). |
| `--heartbeat-seconds` | `MEGOOCI_AGENT_HEARTBEAT_SECONDS` | `15` | Heartbeat interval. |
| `--reconnect-min` | — | `1s` | Initial backoff after connection loss. |
| `--reconnect-max` | — | `30s` | Backoff ceiling. |
| `--log-level` | `MEGOOCI_AGENT_LOG_LEVEL` | `info` | `debug`/`info`/`warn`/`error`. |
| `--insecure-skip-verify` | — | `false` | Skip TLS verification (dev only). |
| `--docker-cleanup-hours` | `MEGOOCI_AGENT_DOCKER_CLEANUP_HOURS` | `6` | Hours between Docker prune runs (0 = disabled). |

## What the agent does

1. **Connect** to `wss://<controller>/api/v1/ws/agents/{id}/connect` with the
   bearer token. Auth failure closes the socket with code 4401 and exits
   with a non-zero status — no silent retry.
2. **Hello** — sends version, OS/arch, and capacity so the UI shows the
   correct runtime info.
3. **Heartbeat** — every `--heartbeat-seconds`, reports busy / capacity.
4. **Receive `run_step`** — for each, spawns a subprocess, streams stdout
   and stderr as `log` frames (with sequence numbers), and reports
   `step_started` then `step_finished` with the exit code.
   **Note:** `notify` steps are always executed server-side (they require
   DB access to look up notification channel credentials) and are never
   dispatched to agents.
5. **Receive `cancel_step`** — cancels the matching subprocess via context
   cancellation (sends SIGKILL on POSIX / CtrlBreak on Windows).
6. **Reconnect** on controller restarts with exponential backoff + jitter.
7. **Shut down cleanly** on SIGINT / SIGTERM.

## Local development

```bash
# From the agent/ directory of the monorepo.
make build          # produces bin/megooci-agent
make vet test
make docker         # builds megooci/agent:0.1.0-dev

# Snapshot release (all OS/arch combos into dist/):
make snapshot
```

## Wire protocol

JSON frames over WebSocket. See [`internal/protocol/protocol.go`](internal/protocol/protocol.go)
for the canonical type definitions and the controller-side handler at
[`backend/app/api/v1/agents_ws.py`](../backend/app/api/v1/agents_ws.py).

```
Agent → Controller
{"type":"hello","version":"0.1.0","agent_id":"...","os":"linux","arch":"amd64","capacity":4}
{"type":"heartbeat","busy":1,"capacity":4}
{"type":"step_started","step_id":"..."}
{"type":"log","build_id":"...","step_id":"...","stream":"stdout","seq":1,"content":"..."}
{"type":"step_finished","step_id":"...","exit_code":0,"status":"success"}

Controller → Agent
{"type":"run_step","build_id":"...","step_id":"...","step_name":"...","command":"..."}
{"type":"cancel_step","step_id":"..."}
{"type":"ping"}
```

## Security model

- Token is bcrypt-hashed server-side; the plaintext is shown once at
  registration / rotation. If you lose it, rotate.
- TLS is required in production (Docker pulls also require it for non-
  localhost). `--insecure-skip-verify` exists only for local dev against a
  self-signed controller.
- Subprocesses run under the agent's own UID (1000 in the Docker image).
  For stronger isolation use one agent container per worker, or the
  upcoming Docker / K8s executors.

## License

Apache-2.0 — same as the rest of MegooCI.
