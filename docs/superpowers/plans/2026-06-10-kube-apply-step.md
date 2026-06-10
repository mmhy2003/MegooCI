# `kube_apply` Step Type Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `kube_apply` pipeline step type that applies Kubernetes manifests and waits for the rollout to succeed, authenticated via a kubeconfig stored as a MegooCI secret.

**Architecture:** `kube_apply` is an agent-side step type like `docker_build`/`ssh_exec`. The backend only needs compiler validation — secret interpolation (`interpolate_value` in `build_executor.py:372`) is generic over the whole config dict, and every step type not in `_SERVER_ONLY_TYPES` (`build_executor.py:402`) is dispatched to an agent automatically, so **no server-side step handler is needed**. Execution is a native Go handler in the agent (like `write_file`/`ai_agent`, not synthesized shell) because it must manage a credential file safely.

**Tech Stack:** Python 3.12 / FastAPI (backend), Go 1.22 (agent), pytest + `uv` (backend tests), `go test` (agent tests), kubectl (pinned static binary in the agent image).

**Spec:** `docs/superpowers/specs/2026-06-10-kube-apply-step-design.md`

---

## Environment notes for executors

- **Backend tests:** `cd /opt/megooci/backend && uv run --extra dev pytest ...` (pytest lives in the `dev` optional-dependency group; `pythonpath = ["."]` is already configured in `pyproject.toml`).
- **Go toolchain is NOT installed on this machine.** Run agent tests via Docker:
  ```bash
  docker run --rm -v /opt/megooci/agent:/src -w /src golang:1.22-bookworm \
    sh -c "go mod tidy && go test ./internal/executor/ -v"
  ```
  (`go mod tidy` first because the repo may not have a committed `go.sum`; the Dockerfile does the same.)
- **Git identity is not configured.** Commit with the repo's existing author:
  ```bash
  git -C /opt/megooci -c user.name="Mohamed M. Hammad" -c user.email="mohamed.magdy@slnee.com" commit -m "..."
  ```

## File structure

| File | Action | Responsibility |
|---|---|---|
| `backend/app/services/pipeline_compiler.py` | Modify | Recognize + validate `kube_apply` in pipeline YAML |
| `backend/tests/test_pipeline_compiler.py` | Create | Validation + compilation tests for `kube_apply` |
| `agent/internal/executor/kube_apply.go` | Create | All `kube_apply` logic: arg builders, output parser, kubeconfig file lifecycle, handler (`local.go` is already 933 lines — new step types get their own file) |
| `agent/internal/executor/kube_apply_test.go` | Create | Unit tests for the above |
| `agent/internal/executor/local.go` | Modify | One routing line in `Run()` |
| `agent/Dockerfile` | Modify | Install pinned kubectl |
| `README.md` | Modify | Step type list + Kubernetes example |
| `frontend/src/components/pipeline/docs-panel.tsx` | Modify | In-editor docs entry |
| `backend/app/api/v1/ai_assistant.py` | Modify | Teach the AI pipeline assistant the new step type |

---

### Task 1: Compiler validation (backend)

**Files:**
- Create: `backend/tests/test_pipeline_compiler.py`
- Modify: `backend/app/services/pipeline_compiler.py` (lines 29–46 `STEP_TYPE_KEYS`, and `_validate_step` — insert after the `ssh_exec` branch ending at line 478)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_pipeline_compiler.py`:

```python
"""Validation and compilation tests for the kube_apply step type."""

from app.services.pipeline_compiler import (
    compile_to_build_graph,
    parse_yaml_pipeline,
    validate_pipeline,
)


def _pipeline(step_block: str) -> str:
    """Wrap a YAML step block (indented 6 spaces) in a minimal pipeline."""
    return (
        "version: 1\n"
        "name: test\n"
        "stages:\n"
        "  - name: deploy\n"
        "    steps:\n" + step_block
    )


VALID_FULL = _pipeline(
    "      - name: deploy to prod\n"
    "        kube_apply:\n"
    "          kubeconfig: ${{ secrets.PROD_KUBECONFIG }}\n"
    "          manifests:\n"
    "            - k8s/deployment.yaml\n"
    "            - k8s/service.yaml\n"
    "          namespace: production\n"
    "          context: prod-cluster\n"
    "          timeout: 600\n"
)


def test_valid_full_config_passes():
    assert validate_pipeline(VALID_FULL) == []


def test_valid_minimal_config_passes():
    yaml_doc = _pipeline(
        "      - kube_apply:\n"
        "          kubeconfig: ${{ secrets.KUBECONFIG }}\n"
        "          manifests:\n"
        "            - k8s/\n"
    )
    assert validate_pipeline(yaml_doc) == []


def test_missing_kubeconfig_fails():
    yaml_doc = _pipeline(
        "      - kube_apply:\n"
        "          manifests:\n"
        "            - k8s/deployment.yaml\n"
    )
    errors = validate_pipeline(yaml_doc)
    assert any("requires 'kubeconfig'" in e for e in errors)


def test_missing_manifests_fails():
    yaml_doc = _pipeline(
        "      - kube_apply:\n"
        "          kubeconfig: ${{ secrets.KUBECONFIG }}\n"
    )
    errors = validate_pipeline(yaml_doc)
    assert any("'manifests'" in e for e in errors)


def test_empty_manifests_fails():
    yaml_doc = _pipeline(
        "      - kube_apply:\n"
        "          kubeconfig: ${{ secrets.KUBECONFIG }}\n"
        "          manifests: []\n"
    )
    errors = validate_pipeline(yaml_doc)
    assert any("'manifests'" in e for e in errors)


def test_manifests_must_be_a_list():
    yaml_doc = _pipeline(
        "      - kube_apply:\n"
        "          kubeconfig: ${{ secrets.KUBECONFIG }}\n"
        "          manifests: k8s/deployment.yaml\n"
    )
    errors = validate_pipeline(yaml_doc)
    assert any("must be a list" in e for e in errors)


def test_manifest_entries_must_be_nonempty_strings():
    yaml_doc = _pipeline(
        "      - kube_apply:\n"
        "          kubeconfig: ${{ secrets.KUBECONFIG }}\n"
        "          manifests:\n"
        "            - 42\n"
    )
    errors = validate_pipeline(yaml_doc)
    assert any("non-empty strings" in e for e in errors)


def test_nonpositive_timeout_fails():
    yaml_doc = _pipeline(
        "      - kube_apply:\n"
        "          kubeconfig: ${{ secrets.KUBECONFIG }}\n"
        "          manifests:\n"
        "            - k8s/deployment.yaml\n"
        "          timeout: 0\n"
    )
    errors = validate_pipeline(yaml_doc)
    assert any("timeout must be a positive number" in e for e in errors)


def test_non_numeric_timeout_fails():
    yaml_doc = _pipeline(
        "      - kube_apply:\n"
        "          kubeconfig: ${{ secrets.KUBECONFIG }}\n"
        "          manifests:\n"
        "            - k8s/deployment.yaml\n"
        "          timeout: soon\n"
    )
    errors = validate_pipeline(yaml_doc)
    assert any("timeout must be a positive number" in e for e in errors)


def test_kube_apply_must_be_a_mapping():
    yaml_doc = _pipeline("      - kube_apply: just-a-string\n")
    errors = validate_pipeline(yaml_doc)
    assert any("must be a mapping" in e for e in errors)


def test_compiles_to_kube_apply_step():
    stages = compile_to_build_graph(parse_yaml_pipeline(VALID_FULL))
    step = stages[0]["steps"][0]
    assert step["step_type"] == "kube_apply"
    assert step["name"] == "deploy to prod"
    assert step["config"]["kubeconfig"] == "${{ secrets.PROD_KUBECONFIG }}"
    assert step["config"]["manifests"] == ["k8s/deployment.yaml", "k8s/service.yaml"]
    assert step["config"]["namespace"] == "production"
    assert step["config"]["context"] == "prod-cluster"
    assert step["config"]["timeout"] == 600
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /opt/megooci/backend && uv run --extra dev pytest tests/test_pipeline_compiler.py -v`

Expected: FAIL. The validation tests fail because the validator reports `must have one of: ...` (the `kube_apply` key is unknown), and `test_compiles_to_kube_apply_step` fails because `_detect_step_type` falls through to `("run", {"command": ""})`.

- [ ] **Step 3: Implement**

In `backend/app/services/pipeline_compiler.py`, add `"kube_apply"` to `STEP_TYPE_KEYS`:

```python
STEP_TYPE_KEYS = {
    "run",
    "write_file",
    "docker_login",
    "docker_build",
    "docker_push",
    "git_clone",
    "git_pull",
    "git_push",
    "ssh_exec",
    "kube_apply",
    "wait_webhook",
    "wait_input",
    "copy_files",
    "delete_files",
    "notify",
    "trigger_pipeline",
    "ai_agent",
}
```

In `_validate_step`, insert a new branch after the `ssh_exec` branch (after line 478, before `elif step_type == "wait_webhook":`):

```python
    elif step_type == "kube_apply":
        if not isinstance(value, dict):
            errors.append(f"{prefix}: 'kube_apply' must be a mapping")
        else:
            if not value.get("kubeconfig"):
                errors.append(f"{prefix}: 'kube_apply' requires 'kubeconfig'")
            manifests = value.get("manifests")
            if not manifests:
                errors.append(
                    f"{prefix}: 'kube_apply' requires at least one entry in 'manifests'"
                )
            elif not isinstance(manifests, list):
                errors.append(f"{prefix}: 'kube_apply' 'manifests' must be a list")
            elif not all(isinstance(m, str) and m.strip() for m in manifests):
                errors.append(
                    f"{prefix}: 'kube_apply' 'manifests' entries must be non-empty strings"
                )
            timeout = value.get("timeout")
            if timeout is not None and (
                not isinstance(timeout, (int, float))
                or isinstance(timeout, bool)
                or timeout <= 0
            ):
                errors.append(f"{prefix}: 'kube_apply' timeout must be a positive number")
```

Also update the module docstring's step list (line 14) to mention `kube_apply (apply Kubernetes manifests)` after the `ssh_exec` line.

No other backend change is needed: `_detect_step_type` handles any `STEP_TYPE_KEYS` dict value generically, `interpolate_value` resolves `${{ secrets.X }}` in the whole config, and `kube_apply` is not in `_SERVER_ONLY_TYPES` so `_execute_step` dispatches it to an agent automatically.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /opt/megooci/backend && uv run --extra dev pytest tests/test_pipeline_compiler.py -v`
Expected: all PASS.

Also run the full backend suite to check nothing broke:
Run: `cd /opt/megooci/backend && uv run --extra dev pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd /opt/megooci
git add backend/app/services/pipeline_compiler.py backend/tests/test_pipeline_compiler.py
git -c user.name="Mohamed M. Hammad" -c user.email="mohamed.magdy@slnee.com" \
  commit -m "feat(compiler): validate kube_apply step type"
```

---

### Task 2: Agent — pure helpers (arg builders, output parser, timeout, kubeconfig file)

**Files:**
- Create: `agent/internal/executor/kube_apply.go`
- Create: `agent/internal/executor/kube_apply_test.go`

- [ ] **Step 1: Write the failing tests**

Create `agent/internal/executor/kube_apply_test.go`:

```go
package executor

import (
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"testing"
)

func TestKubectlApplyArgs(t *testing.T) {
	got := kubectlApplyArgs("/tmp/kc", "k8s/deploy.yaml", "prod", "ctx1")
	want := []string{
		"--kubeconfig", "/tmp/kc",
		"apply", "-f", "k8s/deploy.yaml", "-o", "name",
		"-n", "prod",
		"--context", "ctx1",
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("got %v, want %v", got, want)
	}
}

func TestKubectlApplyArgsOmitsOptionalFlags(t *testing.T) {
	got := kubectlApplyArgs("/tmp/kc", "k8s/", "", "")
	want := []string{"--kubeconfig", "/tmp/kc", "apply", "-f", "k8s/", "-o", "name"}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("got %v, want %v", got, want)
	}
}

func TestKubectlRolloutArgs(t *testing.T) {
	got := kubectlRolloutArgs("/tmp/kc", "deployment.apps/web", "prod", "ctx1", 120)
	want := []string{
		"--kubeconfig", "/tmp/kc",
		"rollout", "status", "deployment.apps/web", "--timeout=120s",
		"-n", "prod",
		"--context", "ctx1",
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("got %v, want %v", got, want)
	}
}

func TestParseRolloutTargets(t *testing.T) {
	output := `deployment.apps/web
service/web
configmap/web-config
statefulset.apps/db
daemonset.apps/log-agent
deployment.apps/web
`
	got := parseRolloutTargets(output)
	want := []string{
		"deployment.apps/web",
		"statefulset.apps/db",
		"daemonset.apps/log-agent",
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("got %v, want %v", got, want)
	}
}

func TestParseRolloutTargetsEmptyAndGarbage(t *testing.T) {
	if got := parseRolloutTargets(""); len(got) != 0 {
		t.Errorf("expected no targets for empty output, got %v", got)
	}
	if got := parseRolloutTargets("Warning: something\nno-slash-line\n"); len(got) != 0 {
		t.Errorf("expected no targets for garbage output, got %v", got)
	}
}

func TestKubeApplyTimeoutSec(t *testing.T) {
	cases := []struct {
		name string
		cfg  map[string]interface{}
		want int
	}{
		{"default", map[string]interface{}{}, 300},
		{"int", map[string]interface{}{"timeout": 60}, 60},
		// JSON numbers arrive as float64 after the controller round-trip.
		{"float64", map[string]interface{}{"timeout": float64(90)}, 90},
		{"nonpositive falls back", map[string]interface{}{"timeout": 0}, 300},
		{"garbage falls back", map[string]interface{}{"timeout": "soon"}, 300},
	}
	for _, tc := range cases {
		if got := kubeApplyTimeoutSec(tc.cfg); got != tc.want {
			t.Errorf("%s: got %d, want %d", tc.name, got, tc.want)
		}
	}
}

func TestWriteKubeconfigFile(t *testing.T) {
	dir := t.TempDir()
	path, err := writeKubeconfigFile(dir, "apiVersion: v1\nkind: Config\n")
	if err != nil {
		t.Fatalf("writeKubeconfigFile: %v", err)
	}
	if filepath.Dir(path) != dir {
		t.Errorf("kubeconfig written outside workdir: %s", path)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read back: %v", err)
	}
	if string(data) != "apiVersion: v1\nkind: Config\n" {
		t.Errorf("content mismatch: %q", data)
	}
	if runtime.GOOS != "windows" {
		info, err := os.Stat(path)
		if err != nil {
			t.Fatal(err)
		}
		if info.Mode().Perm() != 0o600 {
			t.Errorf("kubeconfig permissions = %o, want 0600", info.Mode().Perm())
		}
	}
}
```

- [ ] **Step 2: Run tests to verify they fail to compile**

Run:
```bash
docker run --rm -v /opt/megooci/agent:/src -w /src golang:1.22-bookworm \
  sh -c "go mod tidy && go test ./internal/executor/ -v"
```
Expected: compile error — `undefined: kubectlApplyArgs` (etc.).

- [ ] **Step 3: Implement the helpers**

Create `agent/internal/executor/kube_apply.go`:

```go
package executor

import (
	"fmt"
	"os"
	"strings"
)

const defaultKubeApplyTimeoutSec = 300

// kubeApplyTimeoutSec reads the rollout-wait timeout (seconds) from the step
// config. JSON numbers arrive as float64 after the controller round-trip.
func kubeApplyTimeoutSec(cfg map[string]interface{}) int {
	if t, ok := cfg["timeout"]; ok {
		switch v := t.(type) {
		case float64:
			if v > 0 {
				return int(v)
			}
		case int:
			if v > 0 {
				return v
			}
		}
	}
	return defaultKubeApplyTimeoutSec
}

// kubectlApplyArgs builds the kubectl argument list to apply one manifest
// path (file or directory). `-o name` makes the applied resources parseable
// so we know which workloads to wait on afterwards.
func kubectlApplyArgs(kubeconfigPath, manifest, namespace, kubeContext string) []string {
	args := []string{"--kubeconfig", kubeconfigPath, "apply", "-f", manifest, "-o", "name"}
	if namespace != "" {
		args = append(args, "-n", namespace)
	}
	if kubeContext != "" {
		args = append(args, "--context", kubeContext)
	}
	return args
}

// kubectlRolloutArgs builds the kubectl argument list to wait for one
// workload's rollout. kubectl exits non-zero if the rollout isn't ready
// within --timeout.
func kubectlRolloutArgs(kubeconfigPath, resource, namespace, kubeContext string, timeoutSec int) []string {
	args := []string{
		"--kubeconfig", kubeconfigPath,
		"rollout", "status", resource,
		fmt.Sprintf("--timeout=%ds", timeoutSec),
	}
	if namespace != "" {
		args = append(args, "-n", namespace)
	}
	if kubeContext != "" {
		args = append(args, "--context", kubeContext)
	}
	return args
}

// parseRolloutTargets extracts rollout-able workloads (deployments,
// statefulsets, daemonsets) from `kubectl apply -o name` output, deduplicated
// in first-seen order. Lines look like "deployment.apps/web" or "service/api".
func parseRolloutTargets(output string) []string {
	seen := make(map[string]bool)
	var targets []string
	for _, line := range strings.Split(output, "\n") {
		line = strings.TrimSpace(line)
		kind, _, found := strings.Cut(line, "/")
		if !found {
			continue
		}
		kind, _, _ = strings.Cut(strings.ToLower(kind), ".") // deployment.apps → deployment
		switch kind {
		case "deployment", "statefulset", "daemonset":
			if !seen[line] {
				seen[line] = true
				targets = append(targets, line)
			}
		}
	}
	return targets
}

// writeKubeconfigFile writes kubeconfig content to a 0600 temp file inside
// dir and returns its path. The caller is responsible for removing it.
func writeKubeconfigFile(dir, content string) (string, error) {
	f, err := os.CreateTemp(dir, ".kubeconfig-*")
	if err != nil {
		return "", err
	}
	// CreateTemp already uses 0600; chmod defensively in case of platform quirks.
	if err := f.Chmod(0o600); err != nil {
		f.Close()
		os.Remove(f.Name())
		return "", err
	}
	if _, err := f.WriteString(content); err != nil {
		f.Close()
		os.Remove(f.Name())
		return "", err
	}
	if err := f.Close(); err != nil {
		os.Remove(f.Name())
		return "", err
	}
	return f.Name(), nil
}
```

(Task 3 appends the handler to this file and replaces the import block with a larger one — the minimal imports above are correct for this task only.)

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
docker run --rm -v /opt/megooci/agent:/src -w /src golang:1.22-bookworm \
  sh -c "go mod tidy && go vet ./internal/executor/ && go test ./internal/executor/ -v"
```
Expected: all `TestKubectl*`, `TestParseRolloutTargets*`, `TestKubeApplyTimeoutSec`, `TestWriteKubeconfigFile` PASS.

Note: `go mod tidy` may create/update `agent/go.sum` owned by root. If so: `sudo chown $(id -u):$(id -g) /opt/megooci/agent/go.sum` (or leave it; commit it if it was generated — the Dockerfile tolerates either).

- [ ] **Step 5: Commit**

```bash
cd /opt/megooci
git add agent/internal/executor/kube_apply.go agent/internal/executor/kube_apply_test.go agent/go.sum
git -c user.name="Mohamed M. Hammad" -c user.email="mohamed.magdy@slnee.com" \
  commit -m "feat(agent): kube_apply helpers — kubectl args, rollout parsing, kubeconfig file"
```

(If `agent/go.sum` wasn't created, drop it from `git add`.)

---

### Task 3: Agent — `runKubeApply` handler and routing

**Files:**
- Modify: `agent/internal/executor/kube_apply.go` (append handler, extend imports)
- Modify: `agent/internal/executor/local.go` (routing in `Run()`, lines 84–95)
- Modify: `agent/internal/executor/kube_apply_test.go` (append handler tests)

- [ ] **Step 1: Write the failing tests**

Append to `agent/internal/executor/kube_apply_test.go`:

```go
// drainLogs collects everything a handler writes to its logs channel so the
// send never blocks. Returns a function that stops collection and returns
// the accumulated content.
func drainLogs(logs chan LogLine) func() string {
	var mu sync.Mutex
	var b strings.Builder
	done := make(chan struct{})
	go func() {
		defer close(done)
		for line := range logs {
			mu.Lock()
			b.WriteString(line.Content)
			mu.Unlock()
		}
	}()
	return func() string {
		close(logs)
		<-done
		mu.Lock()
		defer mu.Unlock()
		return b.String()
	}
}

func TestRunKubeApplyMissingKubeconfig(t *testing.T) {
	l := NewLocal(Options{})
	logs := make(chan LogLine, 16)
	stop := drainLogs(logs)

	res := l.runKubeApply(context.Background(), Step{
		StepType: "kube_apply",
		Config: map[string]interface{}{
			"manifests": []interface{}{"k8s/deployment.yaml"},
		},
	}, t.TempDir(), logs)

	out := stop()
	if res.Status != "failed" {
		t.Errorf("status = %q, want failed", res.Status)
	}
	if res.Err == nil || !strings.Contains(res.Err.Error(), "kubeconfig") {
		t.Errorf("expected kubeconfig error, got %v", res.Err)
	}
	if !strings.Contains(out, "kubeconfig") {
		t.Errorf("expected kubeconfig mention in logs, got %q", out)
	}
}

func TestRunKubeApplyMissingManifests(t *testing.T) {
	l := NewLocal(Options{})
	logs := make(chan LogLine, 16)
	stop := drainLogs(logs)

	res := l.runKubeApply(context.Background(), Step{
		StepType: "kube_apply",
		Config: map[string]interface{}{
			"kubeconfig": "apiVersion: v1\nkind: Config\n",
		},
	}, t.TempDir(), logs)

	_ = stop()
	if res.Status != "failed" {
		t.Errorf("status = %q, want failed", res.Status)
	}
	if res.Err == nil || !strings.Contains(res.Err.Error(), "manifests") {
		t.Errorf("expected manifests error, got %v", res.Err)
	}
}

func TestRunKubeApplyCleansUpKubeconfigOnFailure(t *testing.T) {
	// Force a failure after the kubeconfig is written by pointing PATH at an
	// empty dir so kubectl can't be found... except LookPath runs before the
	// write. Instead, verify the workdir holds no .kubeconfig-* file after a
	// run that fails at the LookPath or apply stage.
	dir := t.TempDir()
	l := NewLocal(Options{})
	logs := make(chan LogLine, 64)
	stop := drainLogs(logs)

	t.Setenv("PATH", dir) // no kubectl resolvable

	_ = l.runKubeApply(context.Background(), Step{
		StepType: "kube_apply",
		Config: map[string]interface{}{
			"kubeconfig": "apiVersion: v1\nkind: Config\n",
			"manifests":  []interface{}{"k8s/deployment.yaml"},
		},
	}, dir, logs)
	_ = stop()

	leftovers, err := filepath.Glob(filepath.Join(dir, ".kubeconfig-*"))
	if err != nil {
		t.Fatal(err)
	}
	if len(leftovers) != 0 {
		t.Errorf("kubeconfig temp file leaked: %v", leftovers)
	}
}

func TestRunKubeApplyKubectlNotFound(t *testing.T) {
	dir := t.TempDir()
	l := NewLocal(Options{})
	logs := make(chan LogLine, 16)
	stop := drainLogs(logs)

	t.Setenv("PATH", dir) // empty dir → kubectl not resolvable

	res := l.runKubeApply(context.Background(), Step{
		StepType: "kube_apply",
		Config: map[string]interface{}{
			"kubeconfig": "apiVersion: v1\nkind: Config\n",
			"manifests":  []interface{}{"k8s/deployment.yaml"},
		},
	}, dir, logs)

	out := stop()
	if res.Status != "failed" {
		t.Errorf("status = %q, want failed", res.Status)
	}
	if !strings.Contains(out, "kubectl not found on agent") {
		t.Errorf("expected friendly kubectl-missing error, got %q", out)
	}
}
```

Add the now-needed imports to the test file's import block: `"context"`, `"strings"`, `"sync"` (keep the existing ones).

- [ ] **Step 2: Run tests to verify they fail to compile**

Run:
```bash
docker run --rm -v /opt/megooci/agent:/src -w /src golang:1.22-bookworm \
  sh -c "go mod tidy && go test ./internal/executor/ -v"
```
Expected: compile error — `l.runKubeApply undefined`.

- [ ] **Step 3: Implement the handler**

Replace the import block of `agent/internal/executor/kube_apply.go` with:

```go
import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"sync"

	"github.com/megooci/megooci-agent/internal/protocol"
)
```

Append to `agent/internal/executor/kube_apply.go`:

```go
// runKubeApply handles kube_apply steps natively (no shell): write the
// kubeconfig to a 0600 temp file, `kubectl apply` each manifest, then wait
// for the rollout of every applied workload. The kubeconfig file is removed
// before returning regardless of outcome. The kubeconfig content is never
// written to the build log — only file paths and kubectl output are.
func (l *Local) runKubeApply(ctx context.Context, step Step, workdir string, logs chan<- LogLine) Result {
	fail := func(code int, err error) Result {
		logs <- LogLine{Stream: protocol.StreamStderr, Content: err.Error() + "\n"}
		return Result{ExitCode: code, Status: protocol.StatusFailed, Err: err}
	}

	kubeconfig := configStr(step.Config, "kubeconfig")
	if kubeconfig == "" {
		return fail(1, fmt.Errorf("kube_apply: missing 'kubeconfig'. Verify the referenced secret exists and is in scope for this pipeline"))
	}
	manifests := configStrList(step.Config, "manifests")
	if len(manifests) == 0 {
		return fail(1, fmt.Errorf("kube_apply: 'manifests' must list at least one file or directory"))
	}
	namespace := configStr(step.Config, "namespace")
	kubeContext := configStr(step.Config, "context")
	timeoutSec := kubeApplyTimeoutSec(step.Config)

	if _, err := exec.LookPath("kubectl"); err != nil {
		return fail(1, fmt.Errorf("kube_apply: kubectl not found on agent. Rebuild the agent from the current image (kubectl is preinstalled) or install kubectl on the agent host"))
	}

	kcPath, err := writeKubeconfigFile(workdir, kubeconfig)
	if err != nil {
		return fail(1, fmt.Errorf("kube_apply: write kubeconfig: %w", err))
	}
	defer os.Remove(kcPath)

	var applyOutput strings.Builder
	for _, m := range manifests {
		logs <- LogLine{Stream: protocol.StreamStdout, Content: fmt.Sprintf("$ kubectl apply -f %s -o name\n", m)}
		out, code, err := l.runKubectl(ctx, kubectlApplyArgs(kcPath, m, namespace, kubeContext), workdir, step.Env, logs)
		if ctx.Err() != nil {
			return Result{ExitCode: -1, Status: protocol.StatusCancelled, Err: ctx.Err()}
		}
		if err != nil {
			return fail(1, fmt.Errorf("kube_apply: apply %s: %w", m, err))
		}
		if code != 0 {
			return fail(code, fmt.Errorf("kube_apply: 'kubectl apply -f %s' failed with exit code %d", m, code))
		}
		applyOutput.WriteString(out)
	}

	targets := parseRolloutTargets(applyOutput.String())
	if len(targets) == 0 {
		logs <- LogLine{Stream: protocol.StreamStdout, Content: "kube_apply: no deployments/statefulsets/daemonsets applied; skipping rollout wait\n"}
		return Result{ExitCode: 0, Status: protocol.StatusSuccess}
	}

	for _, target := range targets {
		logs <- LogLine{Stream: protocol.StreamStdout, Content: fmt.Sprintf("$ kubectl rollout status %s --timeout=%ds\n", target, timeoutSec)}
		_, code, err := l.runKubectl(ctx, kubectlRolloutArgs(kcPath, target, namespace, kubeContext, timeoutSec), workdir, step.Env, logs)
		if ctx.Err() != nil {
			return Result{ExitCode: -1, Status: protocol.StatusCancelled, Err: ctx.Err()}
		}
		if err != nil {
			return fail(1, fmt.Errorf("kube_apply: rollout status %s: %w", target, err))
		}
		if code != 0 {
			return fail(code, fmt.Errorf("kube_apply: rollout of %s did not become ready within %ds", target, timeoutSec))
		}
	}

	return Result{ExitCode: 0, Status: protocol.StatusSuccess}
}

// runKubectl executes kubectl with args, streaming stdout/stderr to the build
// log while also capturing stdout for parsing. Returns captured stdout and
// the process exit code. A non-zero exit code is NOT an error return — the
// caller decides how to report it.
func (l *Local) runKubectl(ctx context.Context, args []string, workdir string, env map[string]string, logs chan<- LogLine) (string, int, error) {
	cmd := exec.CommandContext(ctx, "kubectl", args...)
	cmd.Dir = workdir
	cmd.Env = mergeEnv(os.Environ(), env)

	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return "", 1, err
	}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		return "", 1, err
	}
	if err := cmd.Start(); err != nil {
		return "", 1, err
	}

	var captured strings.Builder
	var wg sync.WaitGroup
	wg.Add(2)
	go func() {
		defer wg.Done()
		scanner := bufio.NewScanner(stdout)
		scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
		for scanner.Scan() {
			line := scanner.Text() + "\n"
			captured.WriteString(line)
			select {
			case logs <- LogLine{Stream: protocol.StreamStdout, Content: line}:
			case <-ctx.Done():
				return
			}
		}
	}()
	go func() {
		defer wg.Done()
		pipeLines(stderr, protocol.StreamStderr, logs, ctx)
	}()

	waitErr := cmd.Wait()
	wg.Wait()

	exitCode := 0
	if waitErr != nil {
		var exitErr *exec.ExitError
		if errors.As(waitErr, &exitErr) {
			exitCode = exitErr.ExitCode()
		} else {
			return captured.String(), 1, waitErr
		}
	}
	return captured.String(), exitCode, nil
}
```

In `agent/internal/executor/local.go`, add routing in `Run()` directly after the `ai_agent` branch (line 93–95):

```go
	if step.StepType == "ai_agent" {
		return l.runAiAgent(ctx, step, workdir, logs)
	}
	if step.StepType == "kube_apply" {
		return l.runKubeApply(ctx, step, workdir, logs)
	}
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
docker run --rm -v /opt/megooci/agent:/src -w /src golang:1.22-bookworm \
  sh -c "go mod tidy && go vet ./... && go test ./internal/executor/ -v && go build ./..."
```
Expected: vet clean, all tests PASS (including Task 2's), full build succeeds.

- [ ] **Step 5: Commit**

```bash
cd /opt/megooci
git add agent/internal/executor/kube_apply.go agent/internal/executor/kube_apply_test.go agent/internal/executor/local.go
git -c user.name="Mohamed M. Hammad" -c user.email="mohamed.magdy@slnee.com" \
  commit -m "feat(agent): native kube_apply handler — apply manifests, wait for rollout"
```

---

### Task 4: Agent image — install kubectl

**Files:**
- Modify: `agent/Dockerfile` (insert after the Dart SDK section's `ENV PATH` at line 140, before the "Non-root user" section)

- [ ] **Step 1: Pick the version to pin**

Run: `curl -Ls https://dl.k8s.io/release/stable.txt`
Expected: a version string like `v1.33.x`. Use that value below (the snippet shows `v1.33.1` — substitute the actual output).

- [ ] **Step 2: Add the install block**

Insert into `agent/Dockerfile` after line 140 (`ENV PATH="/usr/lib/dart/bin:${PATH}"`):

```dockerfile
# ── kubectl (Kubernetes CLI, pinned) ───────────────────────────────────────
# Required by `kube_apply` pipeline steps. Static binary from the official
# Kubernetes release bucket; arch follows the image platform (amd64/arm64).
ARG KUBECTL_VERSION=v1.33.1
RUN curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/$(dpkg --print-architecture)/kubectl" \
      -o /usr/local/bin/kubectl \
 && chmod 0755 /usr/local/bin/kubectl \
 && kubectl version --client
```

Also extend the image description comment at the top of the Dockerfile (line 2–3): add `kubectl` to the "batteries-included toolchain" list.

- [ ] **Step 3: Verify**

Quick check that the pinned URL exists:
```bash
curl -fsLI "https://dl.k8s.io/release/<PINNED_VERSION>/bin/linux/amd64/kubectl" -o /dev/null -w "%{http_code}\n"
```
Expected: `200`.

Full check (slow but definitive — requires Docker):
```bash
docker build -t megooci/agent:kube-apply-test /opt/megooci/agent
docker run --rm --entrypoint kubectl megooci/agent:kube-apply-test version --client
```
Expected: build succeeds; prints the pinned client version.

- [ ] **Step 4: Commit**

```bash
cd /opt/megooci
git add agent/Dockerfile
git -c user.name="Mohamed M. Hammad" -c user.email="mohamed.magdy@slnee.com" \
  commit -m "feat(agent): ship kubectl in the agent image for kube_apply steps"
```

---

### Task 5: Documentation — README, in-editor docs panel, AI assistant

**Files:**
- Modify: `README.md` (line 28 step-type list; example section around line 156)
- Modify: `frontend/src/components/pipeline/docs-panel.tsx` (lucide import block at lines 5–27; `DOCS` array, after the `ssh_exec` entry ending at line 311)
- Modify: `backend/app/api/v1/ai_assistant.py` (insert before the `### wait_webhook` section, after the ssh_exec auth-resolution paragraph around line 268)

- [ ] **Step 1: Update README.md**

Replace line 28:

```markdown
- **10 built-in step action types** — `run` (shell), `docker_build`, `docker_login`, `docker_push`, `git_clone`, `git_pull`, `git_push`, `ssh_exec`, `wait_webhook`, and `wait_input`. Template interpolation resolves `${{ secrets.NAME }}` and `${{ env.NAME }}` at runtime.
```

with:

```markdown
- **11 built-in step action types** — `run` (shell), `docker_build`, `docker_login`, `docker_push`, `git_clone`, `git_pull`, `git_push`, `ssh_exec`, `kube_apply`, `wait_webhook`, and `wait_input`. Template interpolation resolves `${{ secrets.NAME }}` and `${{ env.NAME }}` at runtime.
```

After the main pipeline example's closing code fence (the example ends with `docker compose up -d` then ` ``` ` around line 156), insert before the "Link the pipeline to a project…" paragraph:

```markdown
To deploy to Kubernetes instead, store a kubeconfig as a secret and use `kube_apply` — it applies the manifests, then waits for every applied Deployment/StatefulSet/DaemonSet to finish rolling out:

​```yaml
  - name: deploy
    when:
      branch: main
    steps:
      - name: deploy to production
        kube_apply:
          kubeconfig: ${{ secrets.PROD_KUBECONFIG }}
          manifests:
            - k8s/deployment.yaml
            - k8s/service.yaml
          namespace: production   # optional
          timeout: 300            # optional rollout wait in seconds (default 300)
​```
```

(Remove the invisible zero-width characters before the backticks — they're only there so this plan's own fencing doesn't break.)

- [ ] **Step 2: Update the in-editor docs panel**

In `frontend/src/components/pipeline/docs-panel.tsx`, add `Boxes` to the lucide-react import list (alphabetical position, after `Bot`):

```tsx
import {
  Bell,
  Bot,
  Boxes,
  ...
```

(Keep the rest of the existing import list unchanged — just add `Boxes`.)

Then add a new `DocSection` entry to the `DOCS` array immediately after the `ssh_exec` entry (after the closing `},` at line 311, before the `wait_webhook` entry):

```tsx
  {
    id: "kube_apply",
    title: "Kubernetes Apply",
    icon: <Boxes className="h-4 w-4" />,
    description:
      "Apply Kubernetes manifests and wait for the rollout to become ready. Store the kubeconfig as a secret — never inline it. The build fails if any apply fails or a workload doesn't become ready within the timeout.",
    yaml: `- kube_apply:
    kubeconfig: \${{ secrets.PROD_KUBECONFIG }}
    manifests:
      - k8s/deployment.yaml
      - k8s/service.yaml
    namespace: production   # optional
    context: prod-cluster   # optional kubeconfig context
    timeout: 300            # optional rollout wait in seconds (default 300)`,
  },
```

- [ ] **Step 3: Update the AI assistant's step-type reference**

In `backend/app/api/v1/ai_assistant.py`, the ssh_exec section ends with the paragraph beginning `Authentication is resolved in order:` (around line 263–266), followed by `### wait_webhook — Pause until an external webhook callback`. Insert between them:

````markdown
### kube_apply — Apply Kubernetes manifests and wait for rollout
```yaml
- kube_apply:
    kubeconfig: ${{ secrets.PROD_KUBECONFIG }}
    manifests:
      - k8s/deployment.yaml
      - k8s/service.yaml
    namespace: production    # optional
    context: prod-cluster    # optional kubeconfig context
    timeout: 300             # optional rollout wait in seconds (default 300)
```

The kubeconfig must come from a secret — never inline it. After applying, the
step waits for every applied Deployment/StatefulSet/DaemonSet to finish
rolling out and fails the build if any doesn't become ready within the
timeout. Directory entries in `manifests` are applied non-recursively.
````

(Match the surrounding string's exact indentation — this text lives inside a Python string literal; check whether neighboring sections are indented and mirror them.)

- [ ] **Step 4: Verify**

```bash
cd /opt/megooci/frontend && npm run lint
```
Expected: no new errors (run may require `npm install` first if `node_modules` is missing).

```bash
cd /opt/megooci/backend && uv run python -c "import app.api.v1.ai_assistant"
```
Expected: imports cleanly (catches a broken string literal).

- [ ] **Step 5: Commit**

```bash
cd /opt/megooci
git add README.md frontend/src/components/pipeline/docs-panel.tsx backend/app/api/v1/ai_assistant.py
git -c user.name="Mohamed M. Hammad" -c user.email="mohamed.magdy@slnee.com" \
  commit -m "docs: document kube_apply step type in README, docs panel, and AI assistant"
```

---

### Task 6: Final verification

- [ ] **Step 1: Full automated check**

```bash
cd /opt/megooci/backend && uv run --extra dev pytest -q
docker run --rm -v /opt/megooci/agent:/src -w /src golang:1.22-bookworm \
  sh -c "go mod tidy && go vet ./... && go test ./... && go build ./..."
```
Expected: all backend tests pass; agent vet/test/build clean.

- [ ] **Step 2: Manual end-to-end verification (requires a real cluster — document outcome, do not block on it)**

This cannot run in CI (no cluster available). Checklist for whoever has cluster access:

1. Rebuild and restart the agent from the new image: `docker build -t megooci/agent:dev ./agent` and re-run it per the Dockerfile header instructions.
2. In the MegooCI UI, create a secret `PROD_KUBECONFIG` (project or pipeline scope) containing a full kubeconfig file.
3. Create a pipeline:
   ```yaml
   version: 1
   name: kube-apply-e2e
   stages:
     - name: deploy
       steps:
         - git_clone:
             repo: <repo with k8s manifests>
         - name: deploy
           kube_apply:
             kubeconfig: ${{ secrets.PROD_KUBECONFIG }}
             manifests:
               - k8s/
             namespace: default
             timeout: 120
   ```
4. Run a build. Verify: the log shows `$ kubectl apply ...` lines and `rollout status` waits; the build goes green; the kubeconfig content never appears in the log; no `.kubeconfig-*` file remains in the agent workspace after the build.
5. Negative test: point a manifest at a nonexistent image tag → build must go red with the rollout-timeout error.

- [ ] **Step 3: Use superpowers:finishing-a-development-branch** to decide merge/PR handling.

---

## Self-review notes (already applied)

- **Spec coverage:** schema (Task 1), dispatch/secrets (no code needed — verified `interpolate_value` and `_SERVER_ONLY_TYPES` cover it; Task 1 notes), Go handler with 0600 + deferred cleanup + `-o name` parsing + rollout wait + friendly kubectl-missing error (Tasks 2–3), Dockerfile (Task 4), README/docs (Task 5), manual E2E (Task 6). The spec's "Backend unit tests / Agent Go unit tests / E2E" testing section maps to Tasks 1, 2–3, and 6 respectively.
- **Type consistency:** helper names (`kubectlApplyArgs`, `kubectlRolloutArgs`, `parseRolloutTargets`, `kubeApplyTimeoutSec`, `writeKubeconfigFile`, `runKubeApply`, `runKubectl`) are used identically across Tasks 2 and 3. `configStr`/`configStrList`/`mergeEnv`/`pipeLines` already exist in `local.go`.
- **Known simplification:** `timeout` bounds each `rollout status` call individually (via kubectl's `--timeout`), not the sum across workloads. This gives kubectl's clear error message instead of a SIGKILL; acceptable vs the spec's "bounded by timeout" intent and documented in the step docs as "rollout wait".
