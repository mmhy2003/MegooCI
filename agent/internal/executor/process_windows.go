//go:build windows

package executor

import (
	"os/exec"
	"strconv"
	"time"
)

// configureProcessGroup kills the command's whole process tree on cancel.
// Windows has no process groups like POSIX; `taskkill /T /F` terminates the
// process and all its children, which is the dependency-free equivalent.
func configureProcessGroup(cmd *exec.Cmd) {
	cmd.Cancel = func() error {
		if cmd.Process == nil {
			return nil
		}
		kill := exec.Command(
			"taskkill", "/T", "/F", "/PID", strconv.Itoa(cmd.Process.Pid),
		)
		return kill.Run()
	}
	cmd.WaitDelay = 5 * time.Second
}
