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
