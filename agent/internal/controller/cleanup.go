// Periodic Docker cleanup to prevent build junk from accumulating on disk.
//
// When enabled (non-zero interval), a background goroutine runs:
//   - docker system prune -f      (dangling images, stopped containers, networks)
//   - docker builder prune -f     (buildx cache)
//
// These are safe, non-destructive operations that only remove unused objects.
// Tagged images actively in use are never deleted.
package controller

import (
	"context"
	"log/slog"
	"os/exec"
	"runtime"
	"strings"
	"time"
)

// DockerCleanupInterval is the default interval between cleanup runs.
// Set to 0 to disable.
const DockerCleanupInterval = 6 * time.Hour

// StartDockerCleanup launches a background goroutine that periodically prunes
// Docker resources. It returns immediately. The goroutine exits when ctx is
// cancelled.
func StartDockerCleanup(ctx context.Context, interval time.Duration, logger *slog.Logger) {
	if interval <= 0 {
		logger.Info("docker cleanup disabled")
		return
	}
	logger.Info("docker cleanup enabled", "interval", interval)

	go func() {
		// Run an initial cleanup shortly after startup (30s grace period
		// so the agent can finish connecting first).
		select {
		case <-ctx.Done():
			return
		case <-time.After(30 * time.Second):
		}
		runDockerCleanup(ctx, logger)

		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				runDockerCleanup(ctx, logger)
			}
		}
	}()
}

func runDockerCleanup(ctx context.Context, logger *slog.Logger) {
	logger.Info("starting docker cleanup")

	cmds := []struct {
		name string
		args []string
	}{
		// Remove dangling images, stopped containers, and unused networks.
		{"docker system prune", []string{"docker", "system", "prune", "-f"}},
		// Remove buildx build cache.
		{"docker builder prune", []string{"docker", "builder", "prune", "-f", "--keep-storage", "2GB"}},
	}

	for _, c := range cmds {
		if err := runCleanupCmd(ctx, c.args, logger); err != nil {
			logger.Warn("docker cleanup command failed",
				"command", c.name, "error", err)
		}
	}

	logger.Info("docker cleanup completed")
}

func runCleanupCmd(ctx context.Context, args []string, logger *slog.Logger) error {
	// Use a 5-minute timeout so a stuck prune doesn't block the agent.
	cmdCtx, cancel := context.WithTimeout(ctx, 5*time.Minute)
	defer cancel()

	var cmd *exec.Cmd
	if runtime.GOOS == "windows" {
		cmd = exec.CommandContext(cmdCtx, args[0], args[1:]...)
	} else {
		cmd = exec.CommandContext(cmdCtx, args[0], args[1:]...)
	}

	output, err := cmd.CombinedOutput()
	if err != nil {
		return err
	}

	trimmed := strings.TrimSpace(string(output))
	if trimmed != "" {
		logger.Debug("cleanup output", "command", args[0]+" "+args[1], "output", trimmed)
	}
	return nil
}
