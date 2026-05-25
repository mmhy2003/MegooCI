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
	"strings"
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

	// Handle write_file natively — no need to shell out for a file write.
	if step.StepType == "write_file" {
		return l.runWriteFile(step, workdir, logs)
	}
	if step.StepType == "copy_files" {
		return l.runCopyFiles(step, workdir, logs)
	}
	if step.StepType == "delete_files" {
		return l.runDeleteFiles(step, workdir, logs)
	}

	// Resolve the command to execute based on step type.
	command := resolveCommand(step)
	if command == "" {
		return Result{ExitCode: 1, Status: protocol.StatusFailed, Err: fmt.Errorf("empty command for step type %q", step.StepType)}
	}

	cmd := buildCommand(ctx, command)
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

// resolveCommand turns a Step into a single shell command string.
// For "run" steps the command is used as-is. For typed steps (docker_build,
// etc.) we synthesize the appropriate CLI invocation from the config map.
func resolveCommand(step Step) string {
	st := step.StepType
	if st == "" || st == "run" {
		if step.Command != "" {
			return step.Command
		}
		if cmd, ok := step.Config["command"].(string); ok {
			return cmd
		}
		return ""
	}

	switch st {
	case "docker_build":
		return buildDockerBuildCmd(step.Config)
	case "docker_push":
		return buildDockerPushCmd(step.Config)
	case "docker_login":
		return buildDockerLoginCmd(step.Config)
	case "git_clone":
		return buildGitCloneCmd(step.Config)
	case "git_pull":
		return buildGitPullCmd(step.Config)
	case "git_push":
		return buildGitPushCmd(step.Config)
	case "ssh_exec":
		return buildSSHExecCmd(step.Config)
	default:
		if step.Command != "" {
			return step.Command
		}
		return ""
	}
}

func configStr(cfg map[string]interface{}, key string) string {
	v, ok := cfg[key]
	if !ok {
		return ""
	}
	switch val := v.(type) {
	case string:
		return val
	case float64:
		// JSON numbers arrive as float64. If it looks like an integer,
		// format without the decimal point (e.g. depth: 1 → "1").
		if val == float64(int64(val)) {
			return fmt.Sprintf("%d", int64(val))
		}
		return fmt.Sprintf("%g", val)
	case int:
		return fmt.Sprintf("%d", val)
	case int64:
		return fmt.Sprintf("%d", val)
	case bool:
		if val {
			return "true"
		}
		return "false"
	default:
		return fmt.Sprintf("%v", val)
	}
}

func configStrList(cfg map[string]interface{}, key string) []string {
	items, ok := cfg[key].([]interface{})
	if !ok {
		return nil
	}
	out := make([]string, 0, len(items))
	for _, item := range items {
		if s, ok := item.(string); ok {
			out = append(out, s)
		}
	}
	return out
}

func configBool(cfg map[string]interface{}, key string) bool {
	if v, ok := cfg[key].(bool); ok {
		return v
	}
	return false
}

func configStrMap(cfg map[string]interface{}, key string) map[string]string {
	m, ok := cfg[key].(map[string]interface{})
	if !ok {
		return nil
	}
	out := make(map[string]string, len(m))
	for k, v := range m {
		switch val := v.(type) {
		case string:
			out[k] = val
		case float64:
			if val == float64(int64(val)) {
				out[k] = fmt.Sprintf("%d", int64(val))
			} else {
				out[k] = fmt.Sprintf("%g", val)
			}
		default:
			out[k] = fmt.Sprintf("%v", v)
		}
	}
	return out
}

func buildDockerBuildCmd(cfg map[string]interface{}) string {
	args := "docker buildx build --load"
	for _, tag := range configStrList(cfg, "tags") {
		args += " -t " + shellQuote(tag)
	}
	context := configStr(cfg, "context")
	if context == "" {
		context = "."
	}

	// Resolve the Dockerfile path the same way Docker's -f does.
	df := configStr(cfg, "dockerfile")
	resolvedDf := df
	if df != "" {
		if !filepath.IsAbs(df) && context != "." {
			resolvedDf = filepath.Join(context, df)
		}
		args += " -f " + shellQuote(resolvedDf)
	}

	if target := configStr(cfg, "target"); target != "" {
		args += " --target " + shellQuote(target)
	}
	if configBool(cfg, "no_cache") {
		args += " --no-cache"
	}
	if platform := configStr(cfg, "platform"); platform != "" {
		args += " --platform " + shellQuote(platform)
	}
	for k, v := range configStrMap(cfg, "build_args") {
		args += " --build-arg " + shellQuote(k+"="+v)
	}
	args += " " + shellQuote(context)

	// Pre-flight check: verify the Dockerfile exists before invoking
	// docker buildx. If it doesn't, print a diagnostic directory listing
	// so the user can see exactly which files are present in the workspace
	// instead of Docker's cryptic "no such file or directory" error.
	checkPath := resolvedDf
	if checkPath == "" {
		checkPath = filepath.Join(context, "Dockerfile")
	}
	var preCheck string
	if runtime.GOOS == "windows" {
		preCheck = fmt.Sprintf(
			`if not exist %s (echo ERROR: Dockerfile not found at '%s' & echo Working directory: %%cd%% & echo Files in context directory '%s': & dir %s 2>nul || echo (directory does not exist) & exit /b 1)`,
			shellQuote(checkPath), checkPath, context, shellQuote(context),
		)
	} else {
		preCheck = fmt.Sprintf(
			`if [ ! -f %s ]; then echo "ERROR: Dockerfile not found at '%s'"; echo "Working directory: $(pwd)"; echo "Files in context directory '%s':"; ls -la %s 2>/dev/null || echo "(directory does not exist)"; exit 1; fi`,
			shellQuote(checkPath), checkPath, context, shellQuote(context),
		)
	}

	return preCheck + " && " + args
}

func buildDockerPushCmd(cfg map[string]interface{}) string {
	tags := configStrList(cfg, "tags")
	if len(tags) == 0 {
		if img := configStr(cfg, "image"); img != "" {
			tags = []string{img}
		}
	}
	if len(tags) == 0 {
		return ""
	}

	parts := make([]string, len(tags))
	for i, tag := range tags {
		parts[i] = "docker push " + shellQuote(tag)
	}
	result := parts[0]
	for _, p := range parts[1:] {
		result += " && " + p
	}
	return result
}

// buildDockerLoginCmd constructs a shell command for `docker login`.
//
// Supports two authentication modes:
//  1. User credentials — username and password provided via ${{ secrets.X }}.
//  2. Deploy tokens — the fixed username "deploy-token" and a token value
//     created under Container Registry → Deploy Tokens (can be global or
//     project-scoped).
//
// If either credential is empty the secret interpolation likely failed
// (typo, wrong scope, or secret not created).
func buildDockerLoginCmd(cfg map[string]interface{}) string {
	user := configStr(cfg, "username")
	pw := configStr(cfg, "password")

	// Guard: --password-stdin requires --username. If either credential
	// is empty the secret interpolation likely failed (typo, wrong scope,
	// or secret not created). Emit a clear diagnostic instead of letting
	// Docker produce the confusing "--password-stdin requires --username"
	// error.
	if user == "" || pw == "" {
		var missing []string
		if user == "" {
			missing = append(missing, "username")
		}
		if pw == "" {
			missing = append(missing, "password")
		}
		msg := fmt.Sprintf(
			"ERROR: docker_login is missing required field(s): %s. Verify that the referenced secrets exist and are in scope for this pipeline.",
			strings.Join(missing, ", "),
		)
		if runtime.GOOS == "windows" {
			return fmt.Sprintf("echo %s & exit /b 1", shellQuote(msg))
		}
		return fmt.Sprintf("echo %s && exit 1", shellQuote(msg))
	}

	args := "docker login -u " + shellQuote(user) + " --password-stdin"

	reg := configStr(cfg, "registry")
	if reg != "" {
		args += " " + shellQuote(reg)
	}
	args = "echo " + shellQuote(pw) + " | " + args
	return args
}

func buildGitCloneCmd(cfg map[string]interface{}) string {
	repo := configStr(cfg, "repo")
	if repo == "" {
		return ""
	}
	// Inject token into HTTPS URL for private repo authentication.
	// The token comes from secret interpolation on the controller side.
	if token := configStr(cfg, "token"); token != "" && strings.HasPrefix(repo, "https://") {
		repo = strings.Replace(repo, "https://", "https://x-access-token:"+token+"@", 1)
	}
	args := "git clone"
	if branch := configStr(cfg, "branch"); branch != "" {
		args += " -b " + shellQuote(branch)
	}
	if depth := configStr(cfg, "depth"); depth != "" {
		args += " --depth " + depth
	}
	args += " " + shellQuote(repo)
	if path := configStr(cfg, "path"); path != "" {
		args += " " + shellQuote(path)
	}
	return args
}

func buildGitPullCmd(cfg map[string]interface{}) string {
	remote := configStr(cfg, "remote")
	if remote == "" {
		remote = "origin"
	}
	args := "git pull " + shellQuote(remote)
	if branch := configStr(cfg, "branch"); branch != "" {
		args += " " + shellQuote(branch)
	}
	return args
}

func buildGitPushCmd(cfg map[string]interface{}) string {
	remote := configStr(cfg, "remote")
	if remote == "" {
		remote = "origin"
	}
	args := "git push"
	if configBool(cfg, "force") {
		args += " --force"
	}
	args += " " + shellQuote(remote)
	if branch := configStr(cfg, "branch"); branch != "" {
		args += " " + shellQuote(branch)
	}
	return args
}

func buildSSHExecCmd(cfg map[string]interface{}) string {
	host := configStr(cfg, "host")
	if host == "" {
		return ""
	}
	user := configStr(cfg, "user")
	port := configStr(cfg, "port")
	if port == "" {
		port = "22"
	}
	password := configStr(cfg, "password")
	commands := configStrList(cfg, "commands")
	if len(commands) == 0 {
		return ""
	}

	target := host
	if user != "" {
		target = user + "@" + host
	}

	remoteScript := commands[0]
	for _, c := range commands[1:] {
		remoteScript += " && " + c
	}

	var cmd string
	if password != "" {
		// Password auth: use sshpass -e (reads $SSHPASS env var).
		// Prepend SSHPASS= inline so /bin/sh sets it for the child process.
		cmd = "SSHPASS=" + shellQuote(password) + " sshpass -e ssh -o StrictHostKeyChecking=no -o PubkeyAuthentication=no"
	} else {
		cmd = "ssh -o StrictHostKeyChecking=no -o BatchMode=yes"
	}
	cmd += " -p " + port
	cmd += " " + target
	cmd += " " + shellQuote(remoteScript)
	return cmd
}

// runWriteFile handles write_file steps using native Go I/O.
// No shell, no escaping issues, works on every OS.
func (l *Local) runWriteFile(step Step, workdir string, logs chan<- LogLine) Result {
	filePath := configStr(step.Config, "path")
	content := configStr(step.Config, "content")

	if filePath == "" {
		return Result{ExitCode: 1, Status: protocol.StatusFailed, Err: fmt.Errorf("write_file: missing 'path'")}
	}

	// Resolve relative paths against the workspace.
	if !filepath.IsAbs(filePath) {
		filePath = filepath.Join(workdir, filePath)
	}

	// Ensure parent directories exist.
	dir := filepath.Dir(filePath)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		logs <- LogLine{Stream: protocol.StreamStderr, Content: fmt.Sprintf("write_file: failed to create directory %s: %v\n", dir, err)}
		return Result{ExitCode: 1, Status: protocol.StatusFailed, Err: err}
	}

	// Write the file.
	if err := os.WriteFile(filePath, []byte(content), 0o644); err != nil {
		logs <- LogLine{Stream: protocol.StreamStderr, Content: fmt.Sprintf("write_file: failed to write %s: %v\n", filePath, err)}
		return Result{ExitCode: 1, Status: protocol.StatusFailed, Err: err}
	}

	logs <- LogLine{Stream: protocol.StreamStdout, Content: fmt.Sprintf("write_file: wrote %s (%d bytes)\n", filePath, len(content))}
	return Result{ExitCode: 0, Status: protocol.StatusSuccess}
}

// runCopyFiles handles copy_files steps using native Go I/O.
func (l *Local) runCopyFiles(step Step, workdir string, logs chan<- LogLine) Result {
	src := configStr(step.Config, "source")
	dst := configStr(step.Config, "destination")

	if src == "" {
		return Result{ExitCode: 1, Status: protocol.StatusFailed, Err: fmt.Errorf("copy_files: missing 'source'")}
	}
	if dst == "" {
		return Result{ExitCode: 1, Status: protocol.StatusFailed, Err: fmt.Errorf("copy_files: missing 'destination'")}
	}

	if !filepath.IsAbs(src) {
		src = filepath.Join(workdir, src)
	}
	if !filepath.IsAbs(dst) {
		dst = filepath.Join(workdir, dst)
	}

	info, err := os.Stat(src)
	if err != nil {
		logs <- LogLine{Stream: protocol.StreamStderr, Content: fmt.Sprintf("copy_files: source not found: %s: %v\n", src, err)}
		return Result{ExitCode: 1, Status: protocol.StatusFailed, Err: err}
	}

	if !info.IsDir() {
		// Single file copy.
		if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
			logs <- LogLine{Stream: protocol.StreamStderr, Content: fmt.Sprintf("copy_files: mkdir failed: %v\n", err)}
			return Result{ExitCode: 1, Status: protocol.StatusFailed, Err: err}
		}
		if err := copyFile(src, dst); err != nil {
			logs <- LogLine{Stream: protocol.StreamStderr, Content: fmt.Sprintf("copy_files: %v\n", err)}
			return Result{ExitCode: 1, Status: protocol.StatusFailed, Err: err}
		}
		logs <- LogLine{Stream: protocol.StreamStdout, Content: fmt.Sprintf("copy_files: copied %s -> %s\n", src, dst)}
		return Result{ExitCode: 0, Status: protocol.StatusSuccess}
	}

	// Directory copy — walk and copy each file.
	count := 0
	err = filepath.WalkDir(src, func(path string, d os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		rel, _ := filepath.Rel(src, path)
		target := filepath.Join(dst, rel)

		if d.IsDir() {
			return os.MkdirAll(target, 0o755)
		}
		if err := copyFile(path, target); err != nil {
			return err
		}
		count++
		return nil
	})
	if err != nil {
		logs <- LogLine{Stream: protocol.StreamStderr, Content: fmt.Sprintf("copy_files: %v\n", err)}
		return Result{ExitCode: 1, Status: protocol.StatusFailed, Err: err}
	}

	logs <- LogLine{Stream: protocol.StreamStdout, Content: fmt.Sprintf("copy_files: copied %s -> %s (%d files)\n", src, dst, count)}
	return Result{ExitCode: 0, Status: protocol.StatusSuccess}
}

// copyFile copies a single file from src to dst, preserving permissions.
func copyFile(src, dst string) error {
	if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
		return fmt.Errorf("mkdir %s: %w", filepath.Dir(dst), err)
	}
	in, err := os.Open(src)
	if err != nil {
		return fmt.Errorf("open %s: %w", src, err)
	}
	defer in.Close()

	out, err := os.Create(dst)
	if err != nil {
		return fmt.Errorf("create %s: %w", dst, err)
	}
	defer out.Close()

	if _, err := io.Copy(out, in); err != nil {
		return fmt.Errorf("copy %s -> %s: %w", src, dst, err)
	}
	return nil
}

// runDeleteFiles handles delete_files steps using native Go I/O.
func (l *Local) runDeleteFiles(step Step, workdir string, logs chan<- LogLine) Result {
	// Collect paths to delete from either "path" (single) or "paths" (list).
	var targets []string

	if single := configStr(step.Config, "path"); single != "" {
		targets = append(targets, single)
	}
	if list, ok := step.Config["paths"]; ok {
		if items, ok := list.([]interface{}); ok {
			for _, item := range items {
				if s, ok := item.(string); ok && s != "" {
					targets = append(targets, s)
				}
			}
		}
	}

	if len(targets) == 0 {
		return Result{ExitCode: 1, Status: protocol.StatusFailed, Err: fmt.Errorf("delete_files: no paths specified")}
	}

	for _, p := range targets {
		if !filepath.IsAbs(p) {
			p = filepath.Join(workdir, p)
		}
		if err := os.RemoveAll(p); err != nil {
			logs <- LogLine{Stream: protocol.StreamStderr, Content: fmt.Sprintf("delete_files: failed to delete %s: %v\n", p, err)}
			return Result{ExitCode: 1, Status: protocol.StatusFailed, Err: err}
		}
		logs <- LogLine{Stream: protocol.StreamStdout, Content: fmt.Sprintf("delete_files: deleted %s\n", p)}
	}

	return Result{ExitCode: 0, Status: protocol.StatusSuccess}
}

func shellQuote(s string) string {
	if runtime.GOOS == "windows" {
		// cmd.exe + Go's os/exec: Go's syscall.EscapeArg escapes embedded
		// double quotes with backslashes when constructing the CreateProcess
		// command line. cmd.exe then passes those backslash-escaped quotes
		// through as literal characters to the child process (e.g. git sees
		// the filename as '"."' instead of '.').
		//
		// Solution: only wrap in double quotes when the value actually
		// contains spaces or cmd.exe metacharacters. Simple values (URLs,
		// paths without spaces, branch names) are safe unquoted.
		if s == "" {
			return `""`
		}
		if !strings.ContainsAny(s, " \t&|<>^%!()\"") {
			return s
		}
		// Value needs quoting: use caret (^) to escape cmd.exe
		// metacharacters inside a double-quoted string, and escape
		// inner double quotes by doubling them (cmd.exe convention).
		s = strings.ReplaceAll(s, `"`, `""`)
		return `"` + s + `"`
	}
	// Unix: single-quote with inner-quote escaping.
	return "'" + strings.ReplaceAll(s, "'", "'\"'\"'") + "'"
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
	// Default: per-*build* shared directory so all steps in the same build
	// can see each other's files (e.g. git_clone → docker_build).
	// The directory is cleaned up when releaseBuildWorkdir is called
	// after all steps for the build have finished.
	dir, err = l.acquireBuildWorkdir(step.BuildID)
	if err != nil {
		return "", nil, err
	}
	return dir, nil, nil
}

// buildWorkspaces tracks per-build workspace directories.
var (
	buildWorkspaces   = make(map[string]string)
	buildWorkspacesMu sync.Mutex
)

// acquireBuildWorkdir returns (or creates) a shared workspace directory for
// the given build ID.  Subsequent calls with the same buildID return the
// same directory.
func (l *Local) acquireBuildWorkdir(buildID string) (string, error) {
	buildWorkspacesMu.Lock()
	defer buildWorkspacesMu.Unlock()

	if dir, ok := buildWorkspaces[buildID]; ok {
		return dir, nil
	}

	base := filepath.Join(os.TempDir(), "megooci-agent")
	if err := os.MkdirAll(base, 0o755); err != nil {
		return "", err
	}
	dir, err := os.MkdirTemp(base, "build-"+safeID(buildID)+"-*")
	if err != nil {
		return "", err
	}
	buildWorkspaces[buildID] = dir
	return dir, nil
}

// ReleaseBuildWorkdir cleans up the shared workspace for a build.
// Called by the WS handler after all steps for the build have finished.
func ReleaseBuildWorkdir(buildID string) {
	buildWorkspacesMu.Lock()
	dir, ok := buildWorkspaces[buildID]
	if ok {
		delete(buildWorkspaces, buildID)
	}
	buildWorkspacesMu.Unlock()

	if ok && dir != "" {
		_ = os.RemoveAll(dir)
	}
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
