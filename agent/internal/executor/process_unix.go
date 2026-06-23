//go:build !windows

package executor

import (
	"os/exec"
	"syscall"
	"time"
)

// configureProcessGroup puts the command (and every descendant) into a new
// process group, then kills the whole group on context cancel. Without this,
// exec.CommandContext SIGKILLs only the direct child (the shell), leaving
// grandchildren like `docker build` / `npm` running.
func configureProcessGroup(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	cmd.Cancel = func() error {
		if cmd.Process == nil {
			return nil
		}
		// Negative PID targets the whole process group.
		return syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
	}
	// Bound the wait so a process ignoring SIGKILL can't wedge cmd.Wait().
	cmd.WaitDelay = 5 * time.Second
}
