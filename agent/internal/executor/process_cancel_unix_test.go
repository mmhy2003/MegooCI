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

// zombieOrGone returns true if pid is absent or is a zombie process.
// On Linux, /proc/<pid>/stat encodes the process state as a single character
// in the third field. The second field (comm) is wrapped in parentheses and
// may itself contain spaces or parentheses, so we locate the LAST ')' and
// take the first non-space token after it as the state character.
// If the file is unreadable (the process vanished between the Kill probe and
// the read), we treat that as "gone" (returns true).
func zombieOrGone(pid int) bool {
	data, err := os.ReadFile("/proc/" + strconv.Itoa(pid) + "/stat")
	if err != nil {
		// Unreadable — process gone.
		return true
	}
	s := string(data)
	// Find the LAST ')' to skip past the comm field.
	idx := strings.LastIndex(s, ")")
	if idx < 0 {
		return false
	}
	rest := strings.TrimLeft(s[idx+1:], " \t")
	return len(rest) > 0 && rest[0] == 'Z'
}

// TestRunCancelKillsProcessTree verifies that cancelling a step kills not just
// the shell but the whole process group — a backgrounded grandchild must die.
//
// Zombie-robustness note: after the process group is killed, the grandchild
// (the backgrounded `sleep`) may linger as a zombie if there is no init
// reaper — e.g. inside a Docker container started WITHOUT --init. A zombie
// still responds to Kill(pid, 0) with nil (not ESRCH), so a naive liveness
// check would falsely report it as alive and fail the test. processAlive
// therefore reads /proc/<pid>/stat and treats state 'Z' as dead. This makes
// the test reliable regardless of whether an init reaper is present, removing
// any --init requirement.
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

// processAlive returns true if pid is still alive (and not a zombie) at the
// end of the window. It treats both ESRCH and zombie state 'Z' as "gone" so
// that the test is reliable inside containers without an init reaper.
func processAlive(pid int, within time.Duration) bool {
	deadline := time.Now().Add(within)
	for {
		// signal 0 probes existence without delivering a signal.
		if err := syscall.Kill(pid, 0); err != nil {
			return false // ESRCH or permission: gone
		}
		// Kill(pid, 0) returns nil for zombie processes; check /proc stat.
		if zombieOrGone(pid) {
			return false
		}
		if time.Now().After(deadline) {
			return true
		}
		time.Sleep(50 * time.Millisecond)
	}
}
