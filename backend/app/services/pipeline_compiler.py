"""
YAML pipeline compiler for megooci.yaml format.

Supports:
- stages with sequential steps
- run commands (shell)
- write_file (create files)
- copy_files / delete_files (file operations)
- ai_agent (AI coding agent)
- docker_login / docker_build / docker_push
  (docker_login supports both user credentials via secrets and deploy tokens;
   deploy tokens use the fixed username 'deploy-token' and a token value)
- git_clone / git_pull / git_push
- ssh_exec (remote commands)
- wait_webhook / wait_input (pipeline gates)
- trigger_pipeline (trigger another pipeline)
- parallel step groups
- when conditions (branch, status)
- env vars at pipeline/stage/step level
- parameters
- secret/env interpolation placeholders (${{ secrets.X }}, ${{ env.X }})
- artifacts collection (stage-level glob paths)
"""

from typing import Any

import yaml

STEP_TYPE_KEYS = {
    "run",
    "write_file",
    "docker_login",
    "docker_build",
    "docker_push",
    "git_clone",
    "git_pull",
    "git_push",
    "ssh_exec",
    "wait_webhook",
    "wait_input",
    "copy_files",
    "delete_files",
    "notify",
    "trigger_pipeline",
    "ai_agent",
}


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
                    {"name": "install", "step_type": "run", "config": {"command": "npm install"}, "env": {...}},
                    {"name": "build-image", "step_type": "docker_build", "config": {"tags": [...], "context": "."}, "env": {...}},
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

    # Artifacts: list of glob patterns to collect after stage completes.
    artifacts_raw = stage_def.get("artifacts")
    artifacts_paths: list[str] | None = None
    if isinstance(artifacts_raw, dict):
        paths = artifacts_raw.get("paths", [])
        if isinstance(paths, list):
            artifacts_paths = [str(p) for p in paths]
    elif isinstance(artifacts_raw, list):
        artifacts_paths = [str(p) for p in artifacts_raw]

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
        "artifacts": artifacts_paths,
        "steps": steps,
    }


# Allowed values for the `os` selector. Keeping this list small avoids the
# "linux vs Linux vs LINUX" footgun and matches what we expose in the
# agent registration UI.
SUPPORTED_OS = {"linux", "windows", "darwin"}
SUPPORTED_ARCH = {"amd64", "arm64", "x86_64", "aarch64"}


def normalize_runs_on(value: Any) -> dict[str, Any] | None:
    """Normalize a pipeline-level ``runs_on`` declaration into a canonical dict.

    Accepted shapes:
      runs_on: linux                    → {"os": "linux"}
      runs_on: { os: linux }            → {"os": "linux"}
      runs_on: { os: linux, labels: [docker], arch: amd64 }
      runs_on: { labels: [gpu] }        → {"labels": ["gpu"]}

    Returns ``None`` when no constraint is declared. Validation of the
    individual values is done in ``validate_pipeline`` so this stays
    permissive (it should not raise on user input it has already seen pass
    validation upstream).
    """
    if value is None:
        return None
    if isinstance(value, str):
        v = value.strip().lower()
        return {"os": v} if v else None
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        os_val = value.get("os")
        if isinstance(os_val, str) and os_val.strip():
            out["os"] = os_val.strip().lower()
        arch_val = value.get("arch")
        if isinstance(arch_val, str) and arch_val.strip():
            out["arch"] = arch_val.strip().lower()
        labels_val = value.get("labels")
        if isinstance(labels_val, list):
            labels = [str(l).strip() for l in labels_val if str(l).strip()]
            if labels:
                out["labels"] = labels
        return out or None
    return None


def _compile_step(
    step_def: str | dict[str, Any],
    parent_env: dict[str, str],
    default_index: int = 0,
) -> dict[str, Any]:
    """Compile a single step definition.

    Accepts either a plain string (treated as a run command) or a dict.
    Detects the step type by checking which action key is present.
    """
    if isinstance(step_def, str):
        return {
            "name": f"step-{default_index}",
            "step_type": "run",
            "config": {"command": step_def},
            "env": parent_env,
        }

    name = step_def.get("name", f"step-{default_index}")
    step_env = {**parent_env, **step_def.get("env", {})}
    when = step_def.get("when")

    step_type, config = _detect_step_type(step_def)

    return {
        "name": name,
        "step_type": step_type,
        "config": config,
        "env": step_env,
        "when": when,
    }


def _detect_step_type(step_def: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Detect which step type this definition uses and extract its config.

    Returns (step_type, config_dict).
    """
    for key in STEP_TYPE_KEYS:
        if key not in step_def:
            continue

        value = step_def[key]

        if key == "run":
            return "run", {"command": value}

        if isinstance(value, dict):
            return key, dict(value)

        if isinstance(value, str):
            return key, {"value": value}

        if isinstance(value, list):
            return key, {"items": value}

        return key, {"value": value}

    return "run", {"command": ""}


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

    runs_on = data.get("runs_on")
    if runs_on is not None:
        errors.extend(_validate_runs_on(runs_on))

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

                step_errors = _validate_step(step, stage_name=name or str(i), step_index=j)
                errors.extend(step_errors)

        when = stage.get("when")
        if when is not None and not isinstance(when, dict):
            errors.append(f"Stage '{name or i}': 'when' must be a mapping")

        # `runs_on` is pipeline-level only — flag it here so users don't
        # silently get the wrong behaviour after moving it onto a stage.
        if "runs_on" in stage:
            errors.append(
                f"Stage '{name or i}': 'runs_on' is a pipeline-level field; "
                f"move it to the top of the YAML, not inside the stage."
            )

    return errors


def _validate_runs_on(value: Any) -> list[str]:
    """Validate a top-level ``runs_on`` declaration. Returns a list of error strings."""
    errors: list[str] = []
    prefix = "'runs_on'"

    if isinstance(value, str):
        v = value.strip().lower()
        if v and v not in SUPPORTED_OS:
            errors.append(
                f"{prefix}: unknown os '{value}' (supported: {', '.join(sorted(SUPPORTED_OS))})"
            )
        return errors

    if not isinstance(value, dict):
        errors.append(f"{prefix} must be a string or a mapping")
        return errors

    os_val = value.get("os")
    if os_val is not None:
        if not isinstance(os_val, str):
            errors.append(f"{prefix}.os must be a string")
        elif os_val.strip().lower() not in SUPPORTED_OS:
            errors.append(
                f"{prefix}.os: unknown os '{os_val}' (supported: {', '.join(sorted(SUPPORTED_OS))})"
            )

    arch_val = value.get("arch")
    if arch_val is not None:
        if not isinstance(arch_val, str):
            errors.append(f"{prefix}.arch must be a string")
        elif arch_val.strip().lower() not in SUPPORTED_ARCH:
            errors.append(
                f"{prefix}.arch: unknown arch '{arch_val}' (supported: {', '.join(sorted(SUPPORTED_ARCH))})"
            )

    labels_val = value.get("labels")
    if labels_val is not None:
        if not isinstance(labels_val, list):
            errors.append(f"{prefix}.labels must be a list of strings")
        else:
            for l in labels_val:
                if not isinstance(l, str):
                    errors.append(f"{prefix}.labels: each entry must be a string")
                    break

    if not any(value.get(k) for k in ("os", "arch", "labels")):
        errors.append(f"{prefix} must declare at least one of: os, arch, labels")

    return errors


def _validate_step(step: dict[str, Any], stage_name: str, step_index: int) -> list[str]:
    """Validate a single step definition. Returns a list of errors."""
    errors: list[str] = []
    prefix = f"Stage '{stage_name}', step {step_index}"

    found_types = [k for k in STEP_TYPE_KEYS if k in step]

    if not found_types:
        errors.append(
            f"{prefix}: must have one of: {', '.join(sorted(STEP_TYPE_KEYS))}"
        )
        return errors

    if len(found_types) > 1:
        errors.append(
            f"{prefix}: has multiple action types ({', '.join(found_types)}); only one is allowed"
        )
        return errors

    step_type = found_types[0]
    value = step[step_type]

    if step_type == "run":
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{prefix}: 'run' must be a non-empty string")

    elif step_type == "write_file":
        if not isinstance(value, dict):
            errors.append(f"{prefix}: 'write_file' must be a mapping")
        else:
            if not value.get("path"):
                errors.append(f"{prefix}: 'write_file' requires 'path'")
            if "content" not in value:
                errors.append(f"{prefix}: 'write_file' requires 'content'")

    elif step_type == "copy_files":
        if not isinstance(value, dict):
            errors.append(f"{prefix}: 'copy_files' must be a mapping")
        else:
            if not value.get("source"):
                errors.append(f"{prefix}: 'copy_files' requires 'source'")
            if not value.get("destination"):
                errors.append(f"{prefix}: 'copy_files' requires 'destination'")

    elif step_type == "delete_files":
        if not isinstance(value, dict):
            errors.append(f"{prefix}: 'delete_files' must be a mapping")
        else:
            if not value.get("path") and not value.get("paths"):
                errors.append(f"{prefix}: 'delete_files' requires 'path' or 'paths'")

    elif step_type == "ai_agent":
        if not isinstance(value, dict):
            errors.append(f"{prefix}: 'ai_agent' must be a mapping")
        else:
            if not value.get("prompt"):
                errors.append(f"{prefix}: 'ai_agent' requires 'prompt'")
            if not value.get("api_key"):
                errors.append(f"{prefix}: 'ai_agent' requires 'api_key'")
            timeout = value.get("timeout")
            if timeout is not None and (not isinstance(timeout, (int, float)) or timeout <= 0):
                errors.append(f"{prefix}: 'ai_agent' timeout must be a positive number")

    elif step_type == "docker_build":
        if not isinstance(value, dict):
            errors.append(f"{prefix}: 'docker_build' must be a mapping")
        else:
            if not value.get("tags"):
                errors.append(f"{prefix}: 'docker_build' requires at least one tag")

    elif step_type == "docker_push":
        if not isinstance(value, dict):
            errors.append(f"{prefix}: 'docker_push' must be a mapping")
        else:
            if not value.get("tags") and not value.get("image"):
                errors.append(f"{prefix}: 'docker_push' requires 'tags' or 'image'")

    elif step_type == "docker_login":
        if not isinstance(value, dict):
            errors.append(f"{prefix}: 'docker_login' must be a mapping")

    elif step_type == "git_clone":
        if not isinstance(value, dict):
            errors.append(f"{prefix}: 'git_clone' must be a mapping")
        else:
            if not value.get("repo"):
                errors.append(f"{prefix}: 'git_clone' requires 'repo'")

    elif step_type in ("git_pull", "git_push"):
        if value is not None and not isinstance(value, dict):
            errors.append(f"{prefix}: '{step_type}' must be a mapping or null")

    elif step_type == "ssh_exec":
        if not isinstance(value, dict):
            errors.append(f"{prefix}: 'ssh_exec' must be a mapping")
        else:
            if not value.get("host"):
                errors.append(f"{prefix}: 'ssh_exec' requires 'host'")
            if not value.get("commands"):
                errors.append(f"{prefix}: 'ssh_exec' requires 'commands'")

    elif step_type == "wait_webhook":
        if value is not None and not isinstance(value, dict):
            errors.append(f"{prefix}: 'wait_webhook' must be a mapping or null")

    elif step_type == "wait_input":
        if value is not None and not isinstance(value, dict):
            errors.append(f"{prefix}: 'wait_input' must be a mapping or null")

    elif step_type == "notify":
        if not isinstance(value, dict):
            errors.append(f"{prefix}: 'notify' must be a mapping")
        else:
            if not value.get("channel"):
                errors.append(f"{prefix}: 'notify' requires 'channel'")
            if not value.get("message"):
                errors.append(f"{prefix}: 'notify' requires 'message'")

    elif step_type == "trigger_pipeline":
        if not isinstance(value, dict):
            errors.append(f"{prefix}: 'trigger_pipeline' must be a mapping")
        else:
            if not value.get("pipeline"):
                errors.append(f"{prefix}: 'trigger_pipeline' requires 'pipeline'")
            timeout = value.get("timeout")
            if timeout is not None and (not isinstance(timeout, (int, float)) or timeout <= 0):
                errors.append(f"{prefix}: 'trigger_pipeline' timeout must be a positive number")

    return errors
