// Package protocol defines the JSON frame shapes exchanged between the agent
// and the MegooCI controller over the control-plane WebSocket.
//
// The format is intentionally plain JSON so the wire is easy to debug with
// `wscat` / browser devtools. Forward-compat is handled by ignoring unknown
// fields and unknown `type` values.
package protocol

// Message type constants so callers don't have to remember string literals.
const (
	// Agent -> Controller
	TypeHello             = "hello"
	TypeHeartbeat         = "heartbeat"
	TypeLog               = "log"
	TypeStepStarted       = "step_started"
	TypeStepFinished      = "step_finished"
	TypeArtifactsUploaded = "artifacts_uploaded"

	// Controller -> Agent
	TypeRunStep       = "run_step"
	TypeCancelStep    = "cancel_step"
	TypeBuildFinished = "build_finished"
	TypePing          = "ping"

	StreamStdout = "stdout"
	StreamStderr = "stderr"

	StatusSuccess = "success"
	StatusFailed  = "failed"
	StatusCancelled = "cancelled"
)

// Frame is a raw incoming message the agent receives from the controller.
// We decode by `Type` first and then re-parse the concrete shape below.
type Frame struct {
	Type string `json:"type"`
	// Controller -> Agent (run_step)
	AssignmentID string                 `json:"assignment_id,omitempty"`
	BuildID      string                 `json:"build_id,omitempty"`
	StageName    string                 `json:"stage_name,omitempty"`
	StepID       string                 `json:"step_id,omitempty"`
	StepName     string                 `json:"step_name,omitempty"`
	StepType     string                 `json:"step_type,omitempty"`
	Command      string                 `json:"command,omitempty"`
	Config       map[string]interface{} `json:"config,omitempty"`
	Env          map[string]string      `json:"env,omitempty"`
	Workdir      string                 `json:"workdir,omitempty"`
	// ArtifactPaths contains glob patterns of files to collect and upload
	// after this step completes successfully (populated from the pipeline
	// YAML's `artifacts.paths` directive on the parent stage).
	ArtifactPaths []string `json:"artifact_paths,omitempty"`
}

// Hello is the first frame the agent sends after connect.
type Hello struct {
	Type     string `json:"type"`
	Version  string `json:"version"`
	AgentID  string `json:"agent_id"`
	OS       string `json:"os"`
	Arch     string `json:"arch"`
	Capacity int    `json:"capacity"`
}

// Heartbeat is sent periodically while idle or busy.
type Heartbeat struct {
	Type     string `json:"type"`
	Busy     int    `json:"busy"`
	Capacity int    `json:"capacity"`
}

// LogFrame is a single log line streamed back for a step.
type LogFrame struct {
	Type    string `json:"type"`
	BuildID string `json:"build_id"`
	StepID  string `json:"step_id"`
	Stream  string `json:"stream"`
	Seq     int64  `json:"seq"`
	Content string `json:"content"`
}

// StepStarted is sent once the agent has begun executing a step.
type StepStarted struct {
	Type   string `json:"type"`
	StepID string `json:"step_id"`
}

// StepFinished reports the terminal outcome of a step.
type StepFinished struct {
	Type     string `json:"type"`
	StepID   string `json:"step_id"`
	ExitCode int    `json:"exit_code"`
	Status   string `json:"status"`
}

// ArtifactsUploaded is sent after the agent has finished uploading collected
// artifact files for a build step.
type ArtifactsUploaded struct {
	Type    string `json:"type"`
	BuildID string `json:"build_id"`
	StepID  string `json:"step_id"`
	Count   int    `json:"count"`
	Errors  int    `json:"errors"`
}
