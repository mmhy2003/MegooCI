// Package cli wires up the Cobra command tree.
package cli

import (
	"github.com/spf13/cobra"

	"github.com/megooci/megooci-agent/internal/version"
)

// NewRootCmd returns the top-level cobra.Command with all subcommands attached.
func NewRootCmd() *cobra.Command {
	root := &cobra.Command{
		Use:   "megooci-agent",
		Short: "MegooCI self-hosted build agent",
		Long: `MegooCI agent connects to a controller and runs build steps on behalf
of the controller. Start it with ` + "`megooci-agent run`" + `; see ` + "`megooci-agent run --help`" + `
for required flags.`,
		SilenceUsage:  true,
		SilenceErrors: true,
		Version:       version.Full(),
	}

	root.AddCommand(newRunCmd())
	root.AddCommand(newVersionCmd())

	return root
}

func newVersionCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "version",
		Short: "Print the agent version and exit",
		Run: func(cmd *cobra.Command, args []string) {
			cmd.Println(version.Full())
		},
	}
}
