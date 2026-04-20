// Command megooci-agent is the self-hosted MegooCI build agent.
//
// A single static binary that connects outbound to a MegooCI controller over
// WebSocket and executes build steps on behalf of the controller.
//
// Usage:
//
//	megooci-agent run --controller https://megooci.example.com \
//	                   --agent-id 01234567-89ab-cdef-0123-456789abcdef \
//	                   --token megci_agt_xxxxxxxxxxxxxxxxxxxxxxxx
//
// See `megooci-agent --help` for details.
package main

import (
	"fmt"
	"os"

	"github.com/megooci/megooci-agent/internal/cli"
)

func main() {
	if err := cli.NewRootCmd().Execute(); err != nil {
		fmt.Fprintln(os.Stderr, "megooci-agent:", err)
		os.Exit(1)
	}
}
