"""Editing tools for the AI pipeline assistant.

The assistant works on a private copy of the pipeline YAML through a small set
of tools, and the result is offered to the user as a diff:

- ``document``  — the working copy and its editing operations
- ``diff``      — the proposal: new YAML, hunks, counts
- ``reference`` — documentation topics cut from the full system prompt
- ``tools``     — tool definitions for the model and their dispatch
- ``loop``      — the model/tool loop

Nothing here calls an AI provider or touches the database; the API module
supplies both.
"""
