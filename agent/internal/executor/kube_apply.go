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
