"""Validation and compilation tests for the http_request step type."""

import pytest

from app.services.pipeline_compiler import (
    compile_to_build_graph,
    parse_yaml_pipeline,
    validate_pipeline,
    validate_pipeline_definition,
)


def _pipeline(step_block: str) -> str:
    """Wrap a YAML step block (indented 6 spaces) in a minimal pipeline."""
    return (
        "name: test\n"
        "stages:\n"
        "  - name: notify\n"
        "    steps:\n" + step_block
    )


def _step(fields: str) -> str:
    """A pipeline whose only step is http_request with the given field lines
    (each indented 10 spaces)."""
    return _pipeline("      - http_request:\n" + fields)


URL = "          url: https://hooks.example.com/x\n"

VALID_JSON = _pipeline(
    "      - name: announce\n"
    "        http_request:\n"
    "          url: ${{ secrets.TEAMS_WEBHOOK_URL }}\n"
    "          method: POST\n"
    "          headers:\n"
    "            Authorization: Bearer ${{ secrets.DEPLOY_API_TOKEN }}\n"
    "            X-Attempt: 2\n"
    "          json:\n"
    "            text: \"Build #${{ build.number }} deployed\"\n"
    "            count: 3\n"
    "            ok: true\n"
    "            tags: [ci, production]\n"
    "            nested:\n"
    "              branch: ${{ build.branch }}\n"
    "          timeout: 30\n"
    "          retries: 2\n"
    "          expect_status: [200, 202]\n"
    "          verify_tls: false\n"
)

VALID_BODY = _step(
    "          url: https://legacy.example.com/hook\n"
    "          method: put\n"
    "          headers:\n"
    "            Content-Type: application/xml\n"
    "          body: |\n"
    "            <deploy><build>${{ build.number }}</build></deploy>\n"
    "          timeout: 2.5\n"
    "          expect_status: 204\n"
)


def test_valid_json_step_passes():
    assert validate_pipeline(VALID_JSON) == []


def test_valid_body_step_passes():
    assert validate_pipeline(VALID_BODY) == []


def test_minimal_step_needs_only_a_url():
    assert validate_pipeline(_step(URL)) == []


def test_json_may_be_a_list():
    assert validate_pipeline(_step(URL + "          json: [a, b]\n")) == []


def test_url_made_of_a_placeholder_is_accepted():
    assert validate_pipeline(_step("          url: ${{ secrets.HOOK_URL }}\n")) == []


def test_url_with_a_placeholder_inside_is_accepted():
    assert validate_pipeline(_step("          url: ${{ env.BASE }}/hooks/deploy\n")) == []


def test_compiles_to_http_request_step():
    stages = compile_to_build_graph(parse_yaml_pipeline(VALID_JSON))
    step = stages[0]["steps"][0]
    assert step["step_type"] == "http_request"
    assert step["name"] == "announce"
    config = step["config"]
    assert config["url"] == "${{ secrets.TEAMS_WEBHOOK_URL }}"
    assert config["method"] == "POST"
    assert config["headers"] == {
        "Authorization": "Bearer ${{ secrets.DEPLOY_API_TOKEN }}",
        "X-Attempt": 2,
    }
    assert config["json"] == {
        "text": "Build #${{ build.number }} deployed",
        "count": 3,
        "ok": True,
        "tags": ["ci", "production"],
        "nested": {"branch": "${{ build.branch }}"},
    }
    assert config["timeout"] == 30
    assert config["retries"] == 2
    assert config["expect_status"] == [200, 202]
    assert config["verify_tls"] is False


def test_placeholders_inside_json_are_replaced_and_literals_keep_their_types():
    """The server interpolates the whole step config before dispatch; this
    pins that it reaches nested json values and leaves non-strings alone."""
    from app.services.step_actions.interpolation import interpolate_value

    stages = compile_to_build_graph(parse_yaml_pipeline(VALID_JSON))
    config = interpolate_value(
        stages[0]["steps"][0]["config"],
        {"TEAMS_WEBHOOK_URL": "https://hooks.example.com/abc", "DEPLOY_API_TOKEN": "tok"},
        {},
        {"build": {"number": "42", "branch": "main"}},
    )
    assert config["url"] == "https://hooks.example.com/abc"
    assert config["headers"]["Authorization"] == "Bearer tok"
    assert config["json"]["text"] == "Build #42 deployed"
    assert config["json"]["nested"] == {"branch": "main"}
    assert config["json"]["count"] == 3 and config["json"]["ok"] is True
    assert config["json"]["tags"] == ["ci", "production"]


@pytest.mark.parametrize(
    "step_block, expected",
    [
        ("      - http_request: https://example.com/x\n", "'http_request' must be a mapping"),
        ("      - http_request:\n          method: POST\n", "'http_request' requires 'url'"),
        ("      - http_request:\n          url: \"\"\n", "'http_request' requires 'url'"),
        ("      - http_request:\n          url: 42\n", "'http_request' requires 'url'"),
        (
            "      - http_request:\n          url: hooks.example.com/x\n",
            "url must start with http:// or https://",
        ),
        (
            "      - http_request:\n          url: ftp://example.com/x\n",
            "url must start with http:// or https://",
        ),
    ],
)
def test_step_shape_and_url_rules(step_block, expected):
    errors = validate_pipeline(_pipeline(step_block))
    assert any(expected in e for e in errors), errors


@pytest.mark.parametrize(
    "fields, expected",
    [
        ("          method: TRACE\n", "method must be one of: GET, POST, PUT, PATCH, DELETE"),
        ("          method: 5\n", "method must be one of"),
        ("          headers: \"X-A: b\"\n", "headers must be a mapping"),
        ("          headers:\n            X-A: [1, 2]\n", "header names must be strings and values"),
        ("          headers:\n            X-A:\n", "header names must be strings and values"),
        ("          json: {a: 1}\n          body: x\n", "either 'json' or 'body', not both"),
        ("          json: just text\n", "json must be a mapping or a list"),
        ("          json:\n", "json must be a mapping or a list"),
        ("          body: {a: 1}\n", "body must be a string"),
        ("          body: 5\n", "body must be a string"),
        ("          timeout: 0\n", "timeout must be a number greater than 0 and at most 300"),
        ("          timeout: -1\n", "timeout must be a number"),
        ("          timeout: 301\n", "timeout must be a number"),
        ("          timeout: true\n", "timeout must be a number"),
        ("          timeout: \"30\"\n", "timeout must be a number"),
        ("          retries: -1\n", "retries must be a whole number from 0 to 5"),
        ("          retries: 6\n", "retries must be a whole number"),
        ("          retries: 1.5\n", "retries must be a whole number"),
        ("          retries: true\n", "retries must be a whole number"),
        ("          expect_status: 99\n", "expect_status must be a status code"),
        ("          expect_status: 600\n", "expect_status must be a status code"),
        ("          expect_status: []\n", "expect_status must be a status code"),
        ("          expect_status: \"200\"\n", "expect_status must be a status code"),
        ("          expect_status: [200, ok]\n", "expect_status must be a status code"),
        ("          expect_status: true\n", "expect_status must be a status code"),
        ("          verify_tls: \"no\"\n", "verify_tls must be true or false"),
        ("          verify_tls: 0\n", "verify_tls must be true or false"),
    ],
)
def test_field_rules(fields, expected):
    errors = validate_pipeline(_step(URL + fields))
    assert any(expected in e for e in errors), errors


def test_boundary_values_are_accepted():
    fields = (
        URL
        + "          timeout: 300\n"
        + "          retries: 5\n"
        + "          expect_status: [100, 599]\n"
    )
    assert validate_pipeline(_step(fields)) == []
    assert validate_pipeline(_step(URL + "          retries: 0\n")) == []


def test_misspelled_field_is_reported_not_silently_dropped():
    """A typo such as json_body would otherwise send the request with no body."""
    errors = validate_pipeline(_step(URL + "          json_body: {a: 1}\n          header: {}\n"))
    assert any("unknown field(s): header, json_body" in e for e in errors), errors


def test_error_names_the_stage_and_step_and_carries_the_step_line():
    bad = _pipeline(
        "      - run: echo first\n"
        "      - http_request:\n"
        "          method: POST\n"
    )
    errors = validate_pipeline_definition(bad)
    match = [e for e in errors if "requires 'url'" in e.message]
    assert match, errors
    assert match[0].message.startswith("Stage 'notify', step 1: ")
    assert match[0].line == 6  # the http_request step's mapping line


def test_http_request_cannot_be_combined_with_another_action():
    errors = validate_pipeline(_pipeline("      - run: echo hi\n        http_request:\n" + URL))
    assert any("multiple action types" in e for e in errors), errors


def test_step_level_when_and_name_still_work():
    yaml_doc = _pipeline(
        "      - name: on-main\n"
        "        when:\n"
        "          branch: main\n"
        "        http_request:\n" + URL
    )
    assert validate_pipeline(yaml_doc) == []
    step = compile_to_build_graph(parse_yaml_pipeline(yaml_doc))[0]["steps"][0]
    assert step["name"] == "on-main"
    assert step["when"] == {"branch": "main"}
    assert step["step_type"] == "http_request"
