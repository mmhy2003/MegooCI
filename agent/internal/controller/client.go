// Package controller implements the agent side of the MegooCI control-plane
// WebSocket: connect, authenticate, heartbeat, receive step assignments,
// stream logs and report outcomes. Reconnection with exponential backoff is
// handled transparently so operators can ignore controller restarts.
package controller

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"math/rand"
	"mime/multipart"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"

	"github.com/megooci/megooci-agent/internal/executor"
	"github.com/megooci/megooci-agent/internal/protocol"
)

// Options configures a Client.
type Options struct {
	BaseURL      string // e.g. https://megooci.example.com
	AgentID      string
	Token        string
	AgentVersion string

	HeartbeatInterval time.Duration
	ReconnectMin      time.Duration
	ReconnectMax      time.Duration

	InsecureTLS bool

	Executor executor.Executor
	Logger   *slog.Logger
}

// Client runs the agent's control loop.
type Client struct {
	opts   Options
	logger *slog.Logger

	// writeMu guards concurrent writes to the underlying WebSocket: the
	// main read loop, the heartbeat timer, and the per-step goroutines all
	// call sendFrame.
	writeMu sync.Mutex
	conn    *websocket.Conn

	// inFlightCancels maps stepID -> cancel function so cancel_step frames
	// from the controller can stop the corresponding subprocess.
	cancelsMu       sync.Mutex
	inFlightCancels map[string]context.CancelFunc
}

func NewClient(opts Options) *Client {
	if opts.Logger == nil {
		opts.Logger = slog.Default()
	}
	if opts.ReconnectMin == 0 {
		opts.ReconnectMin = time.Second
	}
	if opts.ReconnectMax == 0 {
		opts.ReconnectMax = 30 * time.Second
	}
	if opts.HeartbeatInterval == 0 {
		opts.HeartbeatInterval = 15 * time.Second
	}
	return &Client{
		opts:            opts,
		logger:          opts.Logger,
		inFlightCancels: make(map[string]context.CancelFunc),
	}
}

// Run connects, processes frames, and reconnects with exponential backoff
// until `ctx` is cancelled or a non-recoverable error occurs (auth failure).
func (c *Client) Run(ctx context.Context) error {
	backoff := c.opts.ReconnectMin
	for {
		err := c.runOnce(ctx)
		if err == nil || errors.Is(err, context.Canceled) {
			return nil
		}
		if isAuthError(err) {
			c.logger.Error("authentication rejected by controller; exiting", "error", err)
			return err
		}

		// Exponential backoff with jitter.
		jitter := time.Duration(rand.Int63n(int64(backoff / 4)))
		wait := backoff + jitter
		c.logger.Warn("controller connection lost; reconnecting", "error", err, "retry_in", wait)
		select {
		case <-ctx.Done():
			return nil
		case <-time.After(wait):
		}
		backoff *= 2
		if backoff > c.opts.ReconnectMax {
			backoff = c.opts.ReconnectMax
		}
	}
}

// runOnce opens a single WS session and runs until it disconnects.
func (c *Client) runOnce(ctx context.Context) error {
	wsURL, err := c.websocketURL()
	if err != nil {
		return err
	}

	dialer := *websocket.DefaultDialer
	dialer.HandshakeTimeout = 20 * time.Second
	if c.opts.InsecureTLS {
		dialer.TLSClientConfig = &tls.Config{InsecureSkipVerify: true}
	}

	headers := http.Header{}
	headers.Set("Authorization", "Bearer "+c.opts.Token)
	headers.Set("X-MegooCI-Agent-Token", c.opts.Token)
	headers.Set("User-Agent", "megooci-agent/"+c.opts.AgentVersion)

	c.logger.Info("connecting to controller", "url", redactURL(wsURL))

	conn, resp, err := dialer.DialContext(ctx, wsURL, headers)
	if err != nil {
		if resp != nil {
			defer resp.Body.Close()
			// Auth rejection comes back as HTTP 401 before the WS upgrade.
			if resp.StatusCode == http.StatusUnauthorized ||
				resp.StatusCode == http.StatusForbidden {
				return authError{fmt.Errorf("controller returned %s", resp.Status)}
			}
		}
		return fmt.Errorf("dial: %w", err)
	}
	defer conn.Close()

	c.writeMu.Lock()
	c.conn = conn
	c.writeMu.Unlock()

	// Send hello immediately so the controller knows our version/capacity.
	if err := c.sendFrame(protocol.Hello{
		Type:     protocol.TypeHello,
		Version:  c.opts.AgentVersion,
		AgentID:  c.opts.AgentID,
		OS:       runtime.GOOS,
		Arch:     runtime.GOARCH,
		Capacity: c.opts.Executor.Capacity(),
	}); err != nil {
		return fmt.Errorf("send hello: %w", err)
	}

	c.logger.Info("connected", "agent_id", c.opts.AgentID)

	// Launch heartbeat pings in the background.
	hbCtx, cancelHB := context.WithCancel(ctx)
	defer cancelHB()
	go c.heartbeatLoop(hbCtx)

	// Read loop.
	for {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		_, data, err := conn.ReadMessage()
		if err != nil {
			// 4401 = auth; treat as terminal.
			if ce, ok := err.(*websocket.CloseError); ok && ce.Code == 4401 {
				return authError{err}
			}
			return fmt.Errorf("read: %w", err)
		}

		var frame protocol.Frame
		if err := json.Unmarshal(data, &frame); err != nil {
			c.logger.Warn("bad frame from controller", "error", err)
			continue
		}
		c.handleFrame(ctx, frame)
	}
}

func (c *Client) heartbeatLoop(ctx context.Context) {
	t := time.NewTicker(c.opts.HeartbeatInterval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			if err := c.sendFrame(protocol.Heartbeat{
				Type:     protocol.TypeHeartbeat,
				Busy:     c.inFlightCount(),
				Capacity: c.opts.Executor.Capacity(),
			}); err != nil {
				c.logger.Warn("heartbeat send failed", "error", err)
				return
			}
		}
	}
}

func (c *Client) handleFrame(ctx context.Context, f protocol.Frame) {
	switch f.Type {
	case protocol.TypeRunStep:
		go c.runStep(ctx, f)
	case protocol.TypeCancelStep:
		c.cancelStep(f.StepID)
	case protocol.TypePing:
		// No reply required; the heartbeat loop provides liveness.
	default:
		c.logger.Debug("ignoring unknown frame", "type", f.Type)
	}
}

// runStep executes a single step end-to-end: spawn the subprocess, stream
// logs, and send step_started / step_finished frames. Runs in its own
// goroutine so concurrent steps don't block each other.
func (c *Client) runStep(parent context.Context, f protocol.Frame) {
	if f.StepID == "" {
		c.logger.Warn("run_step frame missing step_id; dropping")
		return
	}
	if f.Command == "" && len(f.Config) == 0 {
		c.logger.Warn("run_step frame missing command and config; dropping", "step_id", f.StepID)
		return
	}

	stepCtx, cancel := context.WithCancel(parent)
	c.registerCancel(f.StepID, cancel)
	defer c.unregisterCancel(f.StepID)
	defer cancel()

	// Notify the controller we started.
	_ = c.sendFrame(protocol.StepStarted{
		Type:   protocol.TypeStepStarted,
		StepID: f.StepID,
	})

	logs := make(chan executor.LogLine, 64)

	// Forward log lines over the WS in order.
	done := make(chan struct{})
	go func() {
		defer close(done)
		var seq int64
		for line := range logs {
			seq++
			_ = c.sendFrame(protocol.LogFrame{
				Type:    protocol.TypeLog,
				BuildID: f.BuildID,
				StepID:  f.StepID,
				Stream:  line.Stream,
				Seq:     seq,
				Content: line.Content,
			})
		}
	}()

	step := executor.Step{
		BuildID:       f.BuildID,
		StepID:        f.StepID,
		Name:          f.StepName,
		StepType:      f.StepType,
		Command:       f.Command,
		Config:        f.Config,
		Env:           f.Env,
		Workdir:       f.Workdir,
		ArtifactPaths: f.ArtifactPaths,
	}

	result := c.opts.Executor.Run(stepCtx, step, logs)

	// Wait for the log-forwarder to drain the closed channel.
	<-done

	_ = c.sendFrame(protocol.StepFinished{
		Type:     protocol.TypeStepFinished,
		StepID:   f.StepID,
		ExitCode: result.ExitCode,
		Status:   result.Status,
	})

	// After a successful step with artifact patterns, collect and upload files.
	if result.Status == protocol.StatusSuccess && len(f.ArtifactPaths) > 0 {
		go c.uploadArtifacts(parent, f.BuildID, f.StepID, step.Workdir, f.ArtifactPaths)
	}

	if result.Err != nil {
		c.logger.Warn("step finished with error", "step_id", f.StepID, "error", result.Err)
	} else {
		c.logger.Info("step finished",
			"step_id", f.StepID, "status", result.Status, "exit_code", result.ExitCode)
	}
}

// ---- small helpers ----

func (c *Client) cancelStep(stepID string) {
	c.cancelsMu.Lock()
	cancel, ok := c.inFlightCancels[stepID]
	c.cancelsMu.Unlock()
	if ok {
		c.logger.Info("cancel_step", "step_id", stepID)
		cancel()
	}
}

func (c *Client) registerCancel(stepID string, cancel context.CancelFunc) {
	c.cancelsMu.Lock()
	c.inFlightCancels[stepID] = cancel
	c.cancelsMu.Unlock()
}

func (c *Client) unregisterCancel(stepID string) {
	c.cancelsMu.Lock()
	delete(c.inFlightCancels, stepID)
	c.cancelsMu.Unlock()
}

func (c *Client) inFlightCount() int {
	c.cancelsMu.Lock()
	defer c.cancelsMu.Unlock()
	return len(c.inFlightCancels)
}

func (c *Client) sendFrame(frame any) error {
	c.writeMu.Lock()
	defer c.writeMu.Unlock()
	if c.conn == nil {
		return errors.New("not connected")
	}
	return c.conn.WriteJSON(frame)
}

// websocketURL turns the controller's base URL into a ws(s) URL at the
// agent-control endpoint, including `?token=` as a fallback for clients
// that can't set Authorization on the upgrade request.
func (c *Client) websocketURL() (string, error) {
	u, err := url.Parse(c.opts.BaseURL)
	if err != nil {
		return "", err
	}
	switch u.Scheme {
	case "http":
		u.Scheme = "ws"
	case "https":
		u.Scheme = "wss"
	default:
		return "", fmt.Errorf("unsupported scheme %q", u.Scheme)
	}
	u.Path = strings.TrimRight(u.Path, "/") +
		"/api/v1/ws/agents/" + c.opts.AgentID + "/connect"
	q := u.Query()
	q.Set("token", c.opts.Token)
	u.RawQuery = q.Encode()
	return u.String(), nil
}

// authError wraps any transport-level error that should not be retried.
type authError struct{ error }

func isAuthError(err error) bool {
	var a authError
	return errors.As(err, &a)
}

// redactURL strips the `token` query param before logging a URL.
func redactURL(raw string) string {
	u, err := url.Parse(raw)
	if err != nil {
		return raw
	}
	q := u.Query()
	if q.Has("token") {
		q.Set("token", "REDACTED")
	}
	u.RawQuery = q.Encode()
	return u.String()
}

// ---- artifact upload ----

// uploadArtifacts collects files matching the given glob patterns relative to
// `workdir` and uploads each as a multipart POST to the controller's artifact
// API at `POST /api/v1/builds/{build_id}/artifacts`.
func (c *Client) uploadArtifacts(ctx context.Context, buildID, stepID, workdir string, patterns []string) {
	if workdir == "" {
		c.logger.Debug("artifact upload skipped: no workdir", "step_id", stepID)
		return
	}

	var matched []string
	for _, pattern := range patterns {
		fullPattern := filepath.Join(workdir, pattern)
		files, err := filepath.Glob(fullPattern)
		if err != nil {
			c.logger.Warn("artifact glob error", "pattern", pattern, "error", err)
			continue
		}
		for _, f := range files {
			fi, err := os.Stat(f)
			if err != nil || fi.IsDir() {
				continue
			}
			matched = append(matched, f)
		}
	}

	if len(matched) == 0 {
		c.logger.Debug("no files matched artifact patterns", "step_id", stepID, "patterns", patterns)
		return
	}

	c.logger.Info("uploading artifacts", "step_id", stepID, "count", len(matched))

	uploaded, errCount := 0, 0
	for _, fpath := range matched {
		rel, _ := filepath.Rel(workdir, fpath)
		if rel == "" {
			rel = filepath.Base(fpath)
		}
		if err := c.uploadSingleArtifact(ctx, buildID, fpath, rel); err != nil {
			c.logger.Warn("artifact upload failed", "file", rel, "error", err)
			errCount++
		} else {
			uploaded++
		}
	}

	_ = c.sendFrame(protocol.ArtifactsUploaded{
		Type:    protocol.TypeArtifactsUploaded,
		BuildID: buildID,
		StepID:  stepID,
		Count:   uploaded,
		Errors:  errCount,
	})
	c.logger.Info("artifact upload complete",
		"step_id", stepID, "uploaded", uploaded, "errors", errCount)
}

// uploadSingleArtifact sends one file to the controller via multipart POST.
func (c *Client) uploadSingleArtifact(ctx context.Context, buildID, filePath, relativePath string) error {
	file, err := os.Open(filePath)
	if err != nil {
		return err
	}
	defer file.Close()

	body := &bytes.Buffer{}
	writer := multipart.NewWriter(body)

	part, err := writer.CreateFormFile("file", filepath.Base(filePath))
	if err != nil {
		return err
	}
	if _, err := io.Copy(part, file); err != nil {
		return err
	}

	// Add relative_path field so the controller stores it under the right name.
	_ = writer.WriteField("relative_path", relativePath)

	if err := writer.Close(); err != nil {
		return err
	}

	// Build the upload URL from the controller base URL.
	uploadURL := strings.TrimRight(c.opts.BaseURL, "/") +
		"/api/v1/builds/" + buildID + "/artifacts"

	req, err := http.NewRequestWithContext(ctx, "POST", uploadURL, body)
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", writer.FormDataContentType())
	req.Header.Set("Authorization", "Bearer "+c.opts.Token)

	tlsCfg := &tls.Config{}
	if c.opts.InsecureTLS {
		tlsCfg.InsecureSkipVerify = true
	}
	client := &http.Client{
		Timeout:   120 * time.Second,
		Transport: &http.Transport{TLSClientConfig: tlsCfg},
	}

	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		respBody, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("upload returned %d: %s", resp.StatusCode, string(respBody))
	}
	return nil
}
