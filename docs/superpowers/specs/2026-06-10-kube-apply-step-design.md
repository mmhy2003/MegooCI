# `kube_apply` Step Type — Design

**Date:** 2026-06-10
**Status:** Approved

## Problem

MegooCI pipelines cannot deploy to Kubernetes. The agent image ships no
`kubectl`, and there is no structured way to handle cluster credentials — a
user would have to hand-write shell in a `run` step that writes
`${{ secrets.KUBECONFIG }}` to a temp file, applies manifests, checks the
rollout, and cleans up. That boilerplate is fragile and easy to get wrong
(file permissions, cleanup, credential leakage into logs). The README already
lists Kubernetes support as planned.

## Decision

Add one new agent-side step type, `kube_apply`, following the same pattern as
`docker_build` and `ssh_exec`. No new stage concept is needed — existing
stages group steps as they always have.

Scope is manifest application only: apply YAML manifests and wait for the
rollout to succeed. Authentication is a kubeconfig stored as a MegooCI secret.

## Pipeline YAML schema

```yaml
- name: deploy to production
  kube_apply:
    kubeconfig: ${{ secrets.PROD_KUBECONFIG }}   # required
    manifests:                                    # required, files or dirs
      - k8s/deployment.yaml
      - k8s/service.yaml
    namespace: production                         # optional; defaults to kubeconfig's
    context: prod-cluster                         # optional kubeconfig context
    timeout: 300                                  # optional; rollout wait in seconds, default 300
```

Multiple clusters or environments are handled by storing one kubeconfig
secret per cluster and referencing the appropriate one per step.

## Components

### Backend: compiler validation

- Add `kube_apply` to `STEP_TYPE_KEYS` in
  `backend/app/services/pipeline_compiler.py`.
- Validation rules:
  - `kubeconfig`: required, non-empty string.
  - `manifests`: required, non-empty list of strings (paths to files or
    directories, relative to the build workspace). Directories are applied
    non-recursively, matching `kubectl apply -f <dir>` default behavior.
  - `namespace`, `context`: optional strings.
  - `timeout`: optional positive number (seconds); default 300. Booleans are
    rejected (Python `bool` is an `int` subclass).

### Backend: dispatch and secrets

- The kubeconfig value is interpolated server-side through the existing
  secret-interpolation mechanism, and masked in logs by the existing
  masking — no new secrets machinery.
- The step is dispatched to an agent over the existing Redis task queue,
  identically to other agent-side step types.

### Agent: execution handler (Go)

Implemented as a native Go handler in
`agent/internal/executor/local.go` (like `write_file`), not a synthesized
shell command, because it must manage a credential file safely:

1. Write the kubeconfig to a temp file with `0600` permissions inside the
   build workspace.
2. For each entry in `manifests`, run
   `kubectl apply -f <path> -o name`, adding `-n <namespace>` and
   `--context <context>` when set, streaming stdout/stderr to the build log.
3. Parse the `-o name` output and collect rollout-able resources
   (`deployment`, `statefulset`, `daemonset`); run
   `kubectl rollout status <resource>` on each, bounded by `timeout`.
4. Delete the kubeconfig temp file in a `defer` — guaranteed cleanup on
   success, failure, or panic.

Failure semantics:

- Any failed `kubectl apply` fails the step, with kubectl's stderr in the
  build log.
- Any rollout that does not become ready within `timeout` fails the step.
- A missing `kubectl` binary produces a clear "kubectl not found on agent"
  error instead of a raw exec failure.

### Agent image

- Install a pinned `kubectl` version in `agent/Dockerfile` so the default
  agent image works out of the box.
- Agents running older images surface the "kubectl not found on agent"
  error above.

### Documentation

- Add `kube_apply` to the step type table and examples in `README.md`.

## Testing

- **Backend unit tests:** compiler validation — valid config, missing
  `kubeconfig`/`manifests`, wrong types, bad `timeout`.
- **Agent Go unit tests:** kubectl argument construction, `-o name` output
  parsing into rollout targets, kubeconfig temp-file lifecycle (created
  with `0600`, deleted on success and on failure).
- **End-to-end:** manual verification against a real cluster, documented as
  a verification step in the implementation plan. Automated CI cannot
  assume a cluster exists.

## Out of scope

- Helm releases, rollback commands, scaling, arbitrary kubectl commands.
- Server-side execution of deploys (this is an agent-side step).
- The "Kubernetes executor" from the README roadmap (running CI steps as
  pods in a cluster) — a separate feature about where steps execute, not
  about deploying applications.
