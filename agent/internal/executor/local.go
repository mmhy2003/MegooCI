package executor

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sync"

	"github.com/megooci/megooci-agent/internal/protocol"
)

// Options configures the local executor.
type Options struct {
	// Workdir, if set, is used as the cwd for every step. If empty, a
	// per-step temp directory is created and removed after the step
	// finishes. Using a temp dir means two steps can't accidentally share
	// files or pollute each other's filesystem.
	Workdir string

	// Capacity is the maximum number of concurrent step executions. The
	// controller trusts the agent to enforce this; the controller does not
	// schedule more than this many in flight.
	Capacity int
}

// Local is an Executor that invokes step commands as child processes.
type Local struct {
	opts Options
	sem  chan struct{} // buffered channel acts as a semaphore for Capacity
}

// NewLocal builds a Local executor. If opts.Capacity <= 0 it defaults to 1.
func NewLocal(opts Options) *Local {
	if opts.Capacity < 1 {
		opts.Capacity = 1
	}
	return &Local{
		opts: opts,
		sem:  make(chan struct{}, opts.Capacity),
	}
}

// Capacity returns the configured max concurrent step count.
func (l *Local) Capacity() int { return l.opts.Capacity }

// Run executes a step and streams its stdout/stderr through the `logs`
// channel. `logs` is closed before Run returns. A cancelled context sends
// SIGKILL to the child process (best-effort).
func (l *Local) Run(ctx context.Context, step Step, logs chan<- LogLine) Result {
	defer close(logs)

	// Acquire a capacity slot. The controller is expected to respect our
	// advertised capacity, but the local semaphore is a belt-and-braces
	// guarantee we never fork more than N children at a time.
	select {
	case l.sem <- struct{}{}:
	case <-ctx.Done():
		return Result{Status: protocol.StatusCancelled, Err: ctx.Err()}
	}
	defer func() { <-l.sem }()

	// Resolve the working directory.
	workdir, cleanup, err := l.resolveWorkdir(step)
	if err != nil {
		return Result{
			ExitCode: 1,
			Status:   protocol.StatusFailed,
			Err:      fmt.Errorf("resolve workdir: %w", err),
		}
	}
	if cleanup != nil {
		defer cleanup()
	}

	// Build the command. Portability: Windows uses cmd.exe /C, POSIX uses
	// /bin/sh -c. Matches the controller's local executor so behaviour is
	// identical across modes.
	cmd := buildCommand(ctx, step.Command)
	cmd.Dir = workdir
	cmd.Env = mergeEnv(os.Environ(), step.Env)

	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return Result{ExitCode: 1, Status: protocol.StatusFailed, Err: err}
	}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		return Result{ExitCode: 1, Status: protocol.StatusFailed, Err: err}
	}

	if err := cmd.Start(); err != nil {
		return Result{ExitCode: 1, Status: protocol.StatusFailed, Err: err}
	}

	// Stream stdout/stderr concurrently so one slow stream never starves
	// the other.
	var wg sync.WaitGroup
	wg.Add(2)
	go func() {
		defer wg.Done()
		pipeLines(stdout, protocol.StreamStdout, logs, ctx)
	}()
	go func() {
		defer wg.Done()
		pipeLines(stderr, protocol.StreamStderr, logs, ctx)
	}()

	waitErr := cmd.Wait()
	wg.Wait()

	// Classify the outcome.
	if ctx.Err() != nil {
		return Result{ExitCode: -1, Status: protocol.StatusCancelled, Err: ctx.Err()}
	}

	exitCode := 0
	if waitErr != nil {
		var exitErr *exec.ExitError
		if errors.As(waitErr, &exitErr) {
			exitCode = exitErr.ExitCode()
		} else {
			return Result{ExitCode: 1, Status: protocol.StatusFailed, Err: waitErr}
		}
	}
	status := protocol.StatusSuccess
	if exitCode != 0 {
		status = protocol.StatusFailed
	}
	return Result{ExitCode: exitCode, Status: status}
}

// ---- helpers ----

func (l *Local) resolveWorkdir(step Step) (dir string, cleanup func(), err error) {
	if step.Workdir != "" {
		return step.Workdir, nil, nil
	}
	if l.opts.Workdir != "" {
		// Reuse a single persistent workdir — useful for local dev.
		if err := os.MkdirAll(l.opts.Workdir, 0o755); err != nil {
			return "", nil, err
		}
		return l.opts.Workdir, nil, nil
	}
	// Default: per-step temp dir, removed after completion.
	base := filepath.Join(os.TempDir(), "megooci-agent")
	if err := os.MkdirAll(base, 0o755); err != nil {
		return "", nil, err
	}
	dir, err = os.MkdirTemp(base, "step-"+safeID(step.StepID)+"-*")
	if err != nil {
		return "", nil, err
	}
	return dir, func() { _ = os.RemoveAll(dir) }, nil
}

func safeID(s string) string {
	// Take the first 8 chars of the step id for debuggability in temp dir
	// listings. Strip any path separators just in case.
	out := make([]byte, 0, 8)
	for i := 0; i < len(s) && len(out) < 8; i++ {
		c := s[i]
		switch {
		case c >= '0' && c <= '9', c >= 'a' && c <= 'z', c >= 'A' && c <= 'Z':
			out = append(out, c)
		}
	}
	if len(out) == 0 {
		return "x"
	}
	return string(out)
}

func buildCommand(ctx context.Context, command string) *exec.Cmd {
	if runtime.GOOS == "windows" {
		return exec.CommandContext(ctx, "cmd.exe", "/C", command)
	}
	return exec.CommandContext(ctx, "/bin/sh", "-c", command)
}

func mergeEnv(parent []string, extra map[string]string) []string {
	if len(extra) == 0 {
		return parent
	}
	// Start from the parent environment; override with per-step values.
	out := make([]string, 0, len(parent)+len(extra))
	overrides := make(map[string]struct{}, len(extra))
	for k := range extra {
		overrides[k] = struct{}{}
	}
	for _, kv := range parent {
		// Keep any parent var not overridden.
		if i := indexOfEqual(kv); i > 0 {
			if _, ok := overrides[kv[:i]]; ok {
				continue
			}
		}
		out = append(out, kv)
	}
	for k, v := range extra {
		out = append(out, k+"="+v)
	}
	return out
}

func indexOfEqual(s string) int {
	for i := 0; i < len(s); i++ {
		if s[i] == '=' {
			return i
		}
	}
	return -1
}

// pipeLines reads `r` line-by-line and forwards each line as a LogLine.
// Stops when `r` closes or `ctx` is cancelled. A scanner is sufficient for
// typical CI output; binary/very-long-line streams are rare.
func pipeLines(r io.Reader, stream string, out chan<- LogLine, ctx context.Context) {
	scanner := bufio.NewScanner(r)
	// Allow lines up to 1 MiB before the scanner complains; big compiler
	// errors can exceed the default 64 KiB.
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for scanner.Scan() {
		line := scanner.Text() + "\n"
		select {
		case out <- LogLine{Stream: stream, Content: line}:
		case <-ctx.Done():
			return
		}
	}
	// Non-fatal: if the scanner hit an error we just stop streaming; the
	// process exit status is the ground truth for success/failure.
}
