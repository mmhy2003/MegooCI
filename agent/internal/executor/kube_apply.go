package executor

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

const defaultKubeApplyTimeoutSec = 300

// kubeApplyTimeoutSec reads the rollout-wait timeout (seconds) from the step
// config. JSON numbers arrive as float64 after the controller round-trip.
func kubeApplyTimeoutSec(cfg map[string]interface{}) int {
	if t, ok := cfg["timeout"]; ok {
		switch v := t.(type) {
		case float64:
			if n := int(v); n > 0 {
				return n
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
