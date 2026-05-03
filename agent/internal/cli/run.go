package cli

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"runtime"
	"strings"
	"syscall"
	"time"

	"github.com/spf13/cobra"

	"github.com/megooci/megooci-agent/internal/controller"
	"github.com/megooci/megooci-agent/internal/executor"
	"github.com/megooci/megooci-agent/internal/version"
)

// runOptions captures the flags users pass to `megooci-agent run`.
type runOptions struct {
	controllerURL   string
	agentID         string
	token           string
	capacity        int
	workdir         string
	heartbeatSec    int
	reconnectMin    time.Duration
	reconnectMax    time.Duration
	logLevel        string
	insecure        bool
	dockerCleanupHrs int
}

func newRunCmd() *cobra.Command {
	opts := runOptions{
		capacity:     1,
		heartbeatSec: 15,
		reconnectMin: 1 * time.Second,
		reconnectMax: 30 * time.Second,
		logLevel:     "info",
	}

	cmd := &cobra.Command{
		Use:   "run",
		Short: "Connect to the controller and execute build steps until stopped",
		Long: `Connects to the MegooCI controller over WebSocket and loops
processing build step assignments until the process is terminated with
SIGINT / SIGTERM.

The agent requires a persistent token issued by the controller when the
agent was registered. If the token is lost, an admin must rotate it in the
UI and re-run the agent with the new value.`,
		RunE: func(cmd *cobra.Command, args []string) error {
			return runAgent(cmd.Context(), opts)
		},
	}

	// Required wiring. Defaults pulled from env so container users can skip
	// flags entirely; see README.
	cmd.Flags().StringVar(&opts.controllerURL, "controller", envOr("MEGOOCI_CONTROLLER_URL", ""),
		"Base URL of the MegooCI controller (e.g. https://megooci.example.com)")
	cmd.Flags().StringVar(&opts.agentID, "agent-id", envOr("MEGOOCI_AGENT_ID", ""),
		"UUID of the agent as shown in the UI after registration")
	cmd.Flags().StringVar(&opts.token, "token", envOr("MEGOOCI_AGENT_TOKEN", ""),
		"Persistent bearer token for this agent (issued at registration)")

	cmd.Flags().IntVar(&opts.capacity, "capacity", envInt("MEGOOCI_AGENT_CAPACITY", 1),
		"Maximum concurrent steps this agent will run")
	cmd.Flags().StringVar(&opts.workdir, "workdir", envOr("MEGOOCI_AGENT_WORKDIR", ""),
		"Working directory for step subprocesses (defaults to a per-step temp dir)")

	cmd.Flags().IntVar(&opts.heartbeatSec, "heartbeat-seconds", envInt("MEGOOCI_AGENT_HEARTBEAT_SECONDS", 15),
		"Seconds between heartbeat frames")
	cmd.Flags().DurationVar(&opts.reconnectMin, "reconnect-min", 1*time.Second,
		"Initial reconnect delay on connection failure")
	cmd.Flags().DurationVar(&opts.reconnectMax, "reconnect-max", 30*time.Second,
		"Maximum reconnect delay (exponential backoff cap)")

	cmd.Flags().StringVar(&opts.logLevel, "log-level", envOr("MEGOOCI_AGENT_LOG_LEVEL", "info"),
		"Log level: debug | info | warn | error")
	cmd.Flags().BoolVar(&opts.insecure, "insecure-skip-verify", false,
		"Skip TLS certificate verification when connecting (dev only)")
	cmd.Flags().IntVar(&opts.dockerCleanupHrs, "docker-cleanup-hours",
		envInt("MEGOOCI_AGENT_DOCKER_CLEANUP_HOURS", 6),
		"Hours between Docker prune runs (0 to disable)")

	return cmd
}

func runAgent(ctx context.Context, opts runOptions) error {
	if err := validateOptions(&opts); err != nil {
		return err
	}

	// Structured logging to stderr so stdout stays clean for any future
	// machine-readable output.
	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{
		Level: parseLogLevel(opts.logLevel),
	}))
	slog.SetDefault(logger)

	logger.Info("starting megooci-agent",
		"version", version.Full(),
		"agent_id", opts.agentID,
		"controller", opts.controllerURL,
		"capacity", opts.capacity,
		"os", runtime.GOOS,
		"arch", runtime.GOARCH,
	)

	// Propagate SIGINT/SIGTERM as a context cancellation so reconnect loops
	// and subprocesses wind down cleanly.
	runCtx, cancel := signal.NotifyContext(ctx, os.Interrupt, syscall.SIGTERM)
	defer cancel()

	exec := executor.NewLocal(executor.Options{
		Workdir:  opts.workdir,
		Capacity: opts.capacity,
	})

	client := controller.NewClient(controller.Options{
		BaseURL:           opts.controllerURL,
		AgentID:           opts.agentID,
		Token:             opts.token,
		AgentVersion:      version.Version,
		HeartbeatInterval: time.Duration(opts.heartbeatSec) * time.Second,
		ReconnectMin:      opts.reconnectMin,
		ReconnectMax:      opts.reconnectMax,
		InsecureTLS:       opts.insecure,
		Executor:          exec,
		Logger:            logger,
	})

	// Start periodic Docker cleanup to prevent build junk from piling up.
	cleanupInterval := time.Duration(opts.dockerCleanupHrs) * time.Hour
	controller.StartDockerCleanup(runCtx, cleanupInterval, logger)

	if err := client.Run(runCtx); err != nil && !errors.Is(err, context.Canceled) {
		return err
	}
	logger.Info("agent shut down cleanly")
	return nil
}

// ---- helpers ----

func validateOptions(o *runOptions) error {
	o.controllerURL = strings.TrimRight(o.controllerURL, "/")
	if o.controllerURL == "" {
		return errors.New("--controller is required")
	}
	if o.agentID == "" {
		return errors.New("--agent-id is required")
	}
	if o.token == "" {
		return errors.New("--token is required")
	}
	if !(strings.HasPrefix(o.controllerURL, "http://") ||
		strings.HasPrefix(o.controllerURL, "https://")) {
		return fmt.Errorf("--controller must start with http:// or https:// (got %q)", o.controllerURL)
	}
	if o.capacity < 1 {
		return errors.New("--capacity must be at least 1")
	}
	if o.heartbeatSec < 1 {
		return errors.New("--heartbeat-seconds must be at least 1")
	}
	return nil
}

func envOr(key, fallback string) string {
	if v, ok := os.LookupEnv(key); ok {
		return v
	}
	return fallback
}

func envInt(key string, fallback int) int {
	if v, ok := os.LookupEnv(key); ok {
		var n int
		if _, err := fmt.Sscanf(v, "%d", &n); err == nil {
			return n
		}
	}
	return fallback
}

func parseLogLevel(s string) slog.Level {
	switch strings.ToLower(strings.TrimSpace(s)) {
	case "debug":
		return slog.LevelDebug
	case "warn", "warning":
		return slog.LevelWarn
	case "error":
		return slog.LevelError
	default:
		return slog.LevelInfo
	}
}
