// Package version exposes build-time metadata for the agent binary.
//
// Values are injected via `-ldflags "-X github.com/megooci/megooci-agent/internal/version.Version=..."`
// in release builds; the defaults are used for local `go run`.
package version

// Version is the semver of the agent binary. Set at link time.
var Version = "0.1.0-dev"

// Commit is the short git SHA of the build.
var Commit = "unknown"

// Date is the ISO-8601 build timestamp.
var Date = "unknown"

// Full returns a human-readable version string for --version and for the
// initial `hello` frame the agent sends the controller.
func Full() string {
	return Version + " (" + Commit + ", " + Date + ")"
}
