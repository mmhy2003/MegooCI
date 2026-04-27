// Package executor runs build steps and streams their output somewhere.
//
// Phase 1 ships a single implementation that invokes commands as local
// subprocesses. Future implementations (Docker, K8s pod, SSH) will satisfy
// the same `Executor` interface so the controller client stays unchanged.
package executor

import "context"

// LogLine is a single stdout/stderr chunk emitted by a running step.
type LogLine struct {
	Stream  string // "stdout" or "stderr"
	Content string // one line, trailing newline preserved for faithful logs
}

// Step is the minimal description the executor needs to run a build step.
type Step struct {
	BuildID       string
	StepID        string
	Name          string
	StepType      string // "run", "docker_build", etc.; empty treated as "run"
	Command       string
	Config        map[string]interface{} // type-specific configuration
	Env           map[string]string
	Workdir       string   // optional; empty = executor's default
	ArtifactPaths []string // glob patterns of files to collect on success
}

// Result is the terminal outcome of a step.
type Result struct {
	ExitCode int
	Status   string // "success" | "failed" | "cancelled"
	Err      error  // non-nil for local process spawn failures
}

// Executor runs steps and streams log lines through `logs`. It blocks until
// the step terminates or the context is cancelled. The returned `Result`
// reports the outcome; `logs` is closed before Run returns.
type Executor interface {
	Run(ctx context.Context, step Step, logs chan<- LogLine) Result
	Capacity() int
}
