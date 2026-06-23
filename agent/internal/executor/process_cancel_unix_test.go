//go:build !windows

package executor

import (
	"context"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

// TestRunCancelKillsProcessTree verifies that cancelling a step kills not just
// the shell but the whole process group — a backgrounded grandchild must die.
func TestRunCancelKillsProcessTree(t *testing.T) {
	dir := t.TempDir()
	pidFile := filepath.Join(dir, "child.pid")

	// Background a long sleep (a grandchild of the agent), record its PID,
	// then wait so the shell stays alive until cancelled.
	command := "sleep 30 & echo $! > " + pidFile + "; wait"

	l := NewLocal(Options{Workdir: dir, Capacity: 1})
	ctx, cancel := context.WithCancel(context.Background())
	logs := make(chan LogLine, 64)
	go func() {
		for range logs {
		}
	}()

	done := make(chan Result, 1)
	go func() {
		done <- l.Run(ctx, Step{
			StepID: "s1", BuildID: "b1", StepType: "run", Command: command,
		}, logs)
	}()

	pid := waitForPID(t, pidFile)
	cancel()

	select {
	case <-done:
	case <-time.After(10 * time.Second):
		t.Fatal("Run did not return within 10s of cancel")
	}

	if processAlive(pid, 3*time.Second) {
		t.Fatalf("grandchild process %d survived cancellation", pid)
	}
}

func waitForPID(t *testing.T, pidFile string) int {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		b, err := os.ReadFile(pidFile)
		if err == nil {
			if s := strings.TrimSpace(string(b)); s != "" {
				pid, perr := strconv.Atoi(s)
				if perr == nil {
					return pid
				}
			}
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Fatal("grandchild never recorded its PID")
	return 0
}

// processAlive returns true if pid is still alive at the end of the window.
func processAlive(pid int, within time.Duration) bool {
	deadline := time.Now().Add(within)
	for {
		// signal 0 probes existence without delivering a signal.
		if err := syscall.Kill(pid, 0); err != nil {
			return false // ESRCH: gone
		}
		if time.Now().After(deadline) {
			return true
		}
		time.Sleep(50 * time.Millisecond)
	}
}
