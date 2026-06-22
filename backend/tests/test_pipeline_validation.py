"""Tests for validate_pipeline_definition — syntax phase (Task 1)."""

from app.services.pipeline_compiler import (
    PipelineError,
    validate_pipeline_definition,
)

VALID = (
    "name: demo\n"
    "stages:\n"
    "  - name: build\n"
    "    steps:\n"
    "      - run: echo hi\n"
)


def test_valid_yaml_has_no_syntax_errors():
    assert validate_pipeline_definition(VALID) == []


def test_pipeline_error_to_dict():
    err = PipelineError(message="boom", line=3, column=5)
    assert err.to_dict() == {
        "message": "boom",
        "line": 3,
        "column": 5,
        "severity": "error",
    }


def test_bad_indentation_reports_line_and_column():
    # The mis-indented "steps:" cannot be parsed as a mapping value.
    bad = (
        "name: demo\n"
        "stages:\n"
        "  - name: build\n"
        "   steps:\n"           # 3-space indent under a 4-space block
        "      - run: echo hi\n"
    )
    errors = validate_pipeline_definition(bad)
    assert len(errors) == 1
    assert errors[0].line is not None
    assert "YAML syntax error" in errors[0].message
    assert f"line {errors[0].line}" in errors[0].message


def test_tab_indentation_is_reported():
    bad = "name: demo\nstages:\n\t- name: build\n"
    errors = validate_pipeline_definition(bad)
    assert len(errors) == 1
    assert errors[0].line is not None
    assert "YAML syntax error" in errors[0].message


def test_unclosed_quote_reports_line():
    bad = 'name: "demo\nstages: []\n'
    errors = validate_pipeline_definition(bad)
    assert len(errors) == 1
    assert errors[0].line is not None


def test_missing_stage_name_has_line():
    bad = (
        "name: demo\n"
        "stages:\n"
        "  - steps:\n"
        "      - run: echo hi\n"
    )
    errors = validate_pipeline_definition(bad)
    assert any("missing a 'name'" in e.message for e in errors)
    err = next(e for e in errors if "missing a 'name'" in e.message)
    assert err.line == 3  # the line of the stage mapping ("- steps:")


def test_empty_input_reports_empty():
    errors = validate_pipeline_definition("")
    assert len(errors) == 1
    assert errors[0].message == "Empty pipeline definition"


def test_none_input_reports_empty():
    errors = validate_pipeline_definition(None)
    assert len(errors) == 1
    assert errors[0].message == "Empty pipeline definition"


def test_kube_apply_missing_kubeconfig_has_line():
    bad = (
        "name: demo\n"
        "stages:\n"
        "  - name: deploy\n"
        "    steps:\n"
        "      - kube_apply:\n"
        "          manifests:\n"
        "            - k8s/\n"
    )
    errors = validate_pipeline_definition(bad)
    match = [e for e in errors if "requires 'kubeconfig'" in e.message]
    assert match, "kube_apply rule did not fire"
    assert match[0].line == 5  # the step mapping line


def test_backcompat_validate_pipeline_returns_strings():
    from app.services.pipeline_compiler import validate_pipeline

    bad = (
        "name: demo\n"
        "stages:\n"
        "  - name: deploy\n"
        "    steps:\n"
        "      - kube_apply:\n"
        "          manifests:\n"
        "            - k8s/\n"
    )
    errors = validate_pipeline(bad)
    assert all(isinstance(e, str) for e in errors)
    assert any("requires 'kubeconfig'" in e for e in errors)


def test_assert_pipeline_valid_raises_with_structured_errors():
    import pytest

    from app.services.pipeline_compiler import (
        PipelineError,
        PipelineValidationError,
        assert_pipeline_valid,
    )

    with pytest.raises(PipelineValidationError) as exc_info:
        assert_pipeline_valid("name: demo\nstages: []\n")

    errors = exc_info.value.errors
    assert errors and all(isinstance(e, PipelineError) for e in errors)


def test_assert_pipeline_valid_passes_for_good_yaml():
    from app.services.pipeline_compiler import assert_pipeline_valid

    assert assert_pipeline_valid(VALID) is None


def test_validation_error_accepts_legacy_string_list():
    from app.services.pipeline_compiler import (
        PipelineError,
        PipelineValidationError,
    )

    err = PipelineValidationError(["Invalid YAML: boom"])
    assert len(err.errors) == 1
    assert isinstance(err.errors[0], PipelineError)
    assert err.errors[0].message == "Invalid YAML: boom"
