"""Documentation topics for the ``reference`` tool, and the tool-mode prompt.

The full system prompt stays the single source of the documentation. In tool
mode the prompt is re-sent on every model call, so the per-step and
per-feature sections are cut out of it here and served on demand.
"""

from __future__ import annotations

import re

_STEP_HEADING = re.compile(r"^### (\w+)", re.MULTILINE)

# "## " sections of the full prompt that become topics, by a word in their heading.
_FEATURE_TOPICS = {
    "Artifacts": "artifacts",
    "Targeting agents": "runs_on",
    "Notifications": "notifications",
    "Pipeline Structure": "structure",
}

TOOL_RULES = """\
## How to work
You edit a private working copy of the user's pipeline YAML with tools. Nothing \
reaches the user's editor until they review your changes as a diff and accept them.

1. Look before you edit. The current YAML is shown below when it is short; \
otherwise use `read_lines` and `search`.
2. Make the smallest change that does what was asked. Prefer `replace_text` for \
small edits and `insert_lines` / `replace_lines` for larger ones. Use \
`write_document` only for a new pipeline or a full rewrite. Never touch parts of \
the pipeline the user did not ask about. Line numbers move when an edit adds or \
removes lines: after such an edit, read again before you use line numbers.
3. Before writing a step or feature you have not just read about, call \
`reference` with its topic. Do not guess field names.
4. When you have changed the YAML, call `validate` and fix every problem it \
reports before you finish.
5. Do NOT paste the pipeline YAML into your reply. The user sees your changes as \
a diff. End with a short explanation of what you changed and why.
6. If the user only asked a question, answer it without editing. A short YAML \
snippet in the answer is fine.
7. Always use `${{ secrets.X }}` for sensitive values — never hardcode passwords, \
tokens or webhook URLs. Use the secret and variable names listed under Project \
Context when there are any.
8. For `notify` steps and the `notifications` block, use a configured channel \
name; ask the user which channel if they have not said.
9. Only add `runs_on` when the user asks for a specific OS, architecture or \
agent, or the work is obviously OS-specific. `list_agents` shows what exists.
10. To be told about failed builds, use the top-level `notifications` block — \
never a `notify` step at the end, which does not run after a failure.
11. When the conversation says an earlier change was proposed but not applied, \
the editor and your working copy do not contain it. To build on it, put its \
YAML into the working copy with `write_document`, then make the new change.
"""


def split_topics(prompt: str) -> dict[str, str]:
    """Cut the full prompt into topics: one per step type, plus the feature
    sections. Keys are what the model passes to ``reference``."""
    topics: dict[str, str] = {}

    sections = re.split(r"(?m)^(?=## )", prompt)
    for section in sections:
        heading = section.split("\n", 1)[0]
        if heading.startswith("## Available Step Types"):
            parts = re.split(r"(?m)^(?=### )", section)
            for part in parts[1:]:
                match = _STEP_HEADING.match(part)
                if match:
                    topics[match.group(1)] = part.strip()
            continue
        for word, topic in _FEATURE_TOPICS.items():
            if heading.startswith(f"## {word}"):
                topics[topic] = section.strip()
    return topics


def _section(prompt: str, heading: str) -> str:
    for section in re.split(r"(?m)^(?=## )", prompt):
        if section.startswith(heading):
            return section.strip()
    return ""


def build_tool_prompt(prompt: str, topics: dict[str, str]) -> str:
    """The shorter system prompt used when the model has tools."""
    intro = prompt.split("\n## ", 1)[0].strip()
    index = ", ".join(f"`{name}`" for name in topics)
    return "\n\n".join(
        part
        for part in (
            intro,
            _section(prompt, "## Pipeline Structure"),
            _section(prompt, "## Placeholders"),
            "## Reference topics\n"
            "Call `reference` with one of these topics to read its documentation: "
            f"{index}.",
            TOOL_RULES.strip(),
        )
        if part
    )


def lookup(topics: dict[str, str], topic: str) -> str | None:
    return topics.get(topic.strip().lower())
