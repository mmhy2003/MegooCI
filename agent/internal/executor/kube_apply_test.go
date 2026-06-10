package executor

import (
	"context"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
	"sync"
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
	got := parseRolloutTargets("deployment.apps/web\r\nservice/api\r\n")
	if !reflect.DeepEqual(got, []string{"deployment.apps/web"}) {
		t.Errorf("CRLF handling: got %v", got)
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
		{"sub-second falls back", map[string]interface{}{"timeout": 0.5}, 300},
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

// writeFakeKubectl installs an executable fake kubectl script into dir and
// points PATH at dir so exec.LookPath resolves it. Skips on Windows.
func writeFakeKubectl(t *testing.T, dir, script string) {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("fake kubectl shell script not supported on windows")
	}
	path := filepath.Join(dir, "kubectl")
	if err := os.WriteFile(path, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir)
}

func TestRunKubeApplyCleansUpKubeconfigOnFailure(t *testing.T) {
	// A fake kubectl that always fails gets the handler past LookPath, so
	// the kubeconfig temp file is genuinely written, the apply stage fails,
	// and the deferred cleanup must remove the file.
	binDir := t.TempDir()
	workdir := t.TempDir()
	writeFakeKubectl(t, binDir, "#!/bin/sh\nexit 1\n")

	l := NewLocal(Options{})
	logs := make(chan LogLine, 64)
	stop := drainLogs(logs)

	res := l.runKubeApply(context.Background(), Step{
		StepType: "kube_apply",
		Config: map[string]interface{}{
			"kubeconfig": "apiVersion: v1\nkind: Config\n",
			"manifests":  []interface{}{"k8s/deployment.yaml"},
		},
	}, workdir, logs)
	_ = stop()

	if res.Status != "failed" {
		t.Errorf("status = %q, want failed", res.Status)
	}
	if res.Err == nil || !strings.Contains(res.Err.Error(), "kubectl apply") {
		t.Errorf("expected failure at the apply stage (kubeconfig already written), got %v", res.Err)
	}
	leftovers, err := filepath.Glob(filepath.Join(workdir, ".kubeconfig-*"))
	if err != nil {
		t.Fatal(err)
	}
	if len(leftovers) != 0 {
		t.Errorf("kubeconfig temp file leaked: %v", leftovers)
	}
}

func TestRunKubeApplyHappyPathWaitsForRollout(t *testing.T) {
	binDir := t.TempDir()
	workdir := t.TempDir()
	writeFakeKubectl(t, binDir, "#!/bin/sh\necho deployment.apps/web\nexit 0\n")

	l := NewLocal(Options{})
	logs := make(chan LogLine, 64)
	stop := drainLogs(logs)

	res := l.runKubeApply(context.Background(), Step{
		StepType: "kube_apply",
		Config: map[string]interface{}{
			"kubeconfig": "apiVersion: v1\nkind: Config\n",
			"manifests":  []interface{}{"k8s/deployment.yaml"},
		},
	}, workdir, logs)

	out := stop()
	if res.Status != "success" || res.ExitCode != 0 {
		t.Fatalf("status=%q exit=%d, want success/0 (logs: %q)", res.Status, res.ExitCode, out)
	}
	if !strings.Contains(out, "rollout status deployment.apps/web") {
		t.Errorf("expected rollout wait for deployment.apps/web in logs, got %q", out)
	}
	leftovers, err := filepath.Glob(filepath.Join(workdir, ".kubeconfig-*"))
	if err != nil {
		t.Fatal(err)
	}
	if len(leftovers) != 0 {
		t.Errorf("kubeconfig temp file leaked on success: %v", leftovers)
	}
}

func TestRunKubeApplyCancelledContext(t *testing.T) {
	binDir := t.TempDir()
	workdir := t.TempDir()
	writeFakeKubectl(t, binDir, "#!/bin/sh\nexit 0\n")

	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	l := NewLocal(Options{})
	logs := make(chan LogLine, 64)
	stop := drainLogs(logs)

	res := l.runKubeApply(ctx, Step{
		StepType: "kube_apply",
		Config: map[string]interface{}{
			"kubeconfig": "apiVersion: v1\nkind: Config\n",
			"manifests":  []interface{}{"k8s/deployment.yaml"},
		},
	}, workdir, logs)
	_ = stop()

	if res.Status != "cancelled" {
		t.Errorf("status = %q, want cancelled", res.Status)
	}
	leftovers, _ := filepath.Glob(filepath.Join(workdir, ".kubeconfig-*"))
	if len(leftovers) != 0 {
		t.Errorf("kubeconfig temp file leaked on cancel: %v", leftovers)
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
