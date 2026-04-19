"""
YAML pipeline compiler for megooci.yaml format.

Supports:
- stages with sequential steps
- run commands (shell)
- parallel step groups
- when conditions (branch, status)
- env vars at pipeline/stage/step level
- parameters
"""

from typing import Any

import yaml


class PipelineValidationError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"Pipeline validation failed: {'; '.join(errors)}")


def parse_yaml_pipeline(yaml_content: str) -> dict[str, Any]:
    """Parse a megooci.yaml string into a pipeline definition dict."""
    try:
        data = yaml.safe_load(yaml_content)
    except yaml.YAMLError as exc:
        raise PipelineValidationError([f"Invalid YAML: {exc}"])

    if data is None:
        raise PipelineValidationError(["Empty pipeline definition"])

    if isinstance(data, list):
        data = {"stages": data}

    if not isinstance(data, dict):
        raise PipelineValidationError(
            ["Pipeline definition must be a mapping or a list of stages"]
        )

    return data


def compile_to_build_graph(pipeline_def: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Convert a parsed pipeline definition into a flat list of stage dicts,
    each containing a list of step dicts ready for execution.

    Returns:
        [
            {
                "name": "build",
                "env": {...},
                "when": {...},
                "parallel": False,
                "steps": [
                    {"name": "install", "run": "npm install", "env": {...}},
                    ...
                ]
            },
            ...
        ]
    """
    stages_raw = pipeline_def.get("stages", [])
    pipeline_env = pipeline_def.get("env", {})
    pipeline_params = pipeline_def.get("parameters", {})

    stages: list[dict[str, Any]] = []

    for stage_def in stages_raw:
        stage = _compile_stage(stage_def, pipeline_env)
        stages.append(stage)

    return stages


def _compile_stage(
    stage_def: dict[str, Any], parent_env: dict[str, str]
) -> dict[str, Any]:
    """Compile a single stage definition."""
    name = stage_def.get("name", "unnamed-stage")
    stage_env = {**parent_env, **stage_def.get("env", {})}
    when = stage_def.get("when")
    is_parallel = stage_def.get("parallel", False)

    steps_raw = stage_def.get("steps", [])
    steps: list[dict[str, Any]] = []

    for i, step_def in enumerate(steps_raw):
        step = _compile_step(step_def, stage_env, default_index=i)
        steps.append(step)

    return {
        "name": name,
        "env": stage_env,
        "when": when,
        "parallel": is_parallel,
        "steps": steps,
    }


def _compile_step(
    step_def: str | dict[str, Any],
    parent_env: dict[str, str],
    default_index: int = 0,
) -> dict[str, Any]:
    """Compile a single step definition.

    Accepts either a plain string (treated as a run command) or a dict.
    """
    if isinstance(step_def, str):
        return {
            "name": f"step-{default_index}",
            "run": step_def,
            "env": parent_env,
        }

    name = step_def.get("name", f"step-{default_index}")
    run_cmd = step_def.get("run")
    step_env = {**parent_env, **step_def.get("env", {})}
    when = step_def.get("when")

    return {
        "name": name,
        "run": run_cmd,
        "env": step_env,
        "when": when,
    }


def validate_pipeline(yaml_content: str) -> list[str]:
    """Validate a YAML pipeline and return a list of error messages (empty = valid)."""
    errors: list[str] = []

    try:
        data = yaml.safe_load(yaml_content)
    except yaml.YAMLError as exc:
        return [f"Invalid YAML syntax: {exc}"]

    if data is None:
        return ["Empty pipeline definition"]

    if isinstance(data, list):
        data = {"stages": data}

    if not isinstance(data, dict):
        return ["Pipeline definition must be a mapping or a list of stages"]

    stages = data.get("stages")
    if not stages:
        errors.append("Pipeline must define at least one stage")
        return errors

    if not isinstance(stages, list):
        errors.append("'stages' must be a list")
        return errors

    stage_names: set[str] = set()
    for i, stage in enumerate(stages):
        if not isinstance(stage, dict):
            errors.append(f"Stage {i} must be a mapping")
            continue

        name = stage.get("name")
        if not name:
            errors.append(f"Stage {i} is missing a 'name' field")
        elif name in stage_names:
            errors.append(f"Duplicate stage name: '{name}'")
        else:
            stage_names.add(name)

        steps = stage.get("steps")
        if not steps:
            errors.append(f"Stage '{name or i}' must define at least one step")
        elif not isinstance(steps, list):
            errors.append(f"Stage '{name or i}': 'steps' must be a list")
        else:
            for j, step in enumerate(steps):
                if isinstance(step, str):
                    continue
                if not isinstance(step, dict):
                    errors.append(
                        f"Stage '{name or i}', step {j}: must be a string or mapping"
                    )
                    continue
                if "run" not in step and "docker_build" not in step and "docker_push" not in step:
                    errors.append(
                        f"Stage '{name or i}', step {j}: must have a 'run', "
                        f"'docker_build', or 'docker_push' command"
                    )

        when = stage.get("when")
        if when is not None and not isinstance(when, dict):
            errors.append(f"Stage '{name or i}': 'when' must be a mapping")

    return errors
