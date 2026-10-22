"""Steps without a `name` get a label built from what they do, not `step-N`."""

import textwrap

import pytest

from app.services.pipeline_compiler import (
    compile_to_build_graph,
    parse_yaml_pipeline,
    validate_pipeline,
)


def _pipeline(steps_yaml: str) -> str:
    return (
        "version: 1\n"
        "name: test\n"
        "stages:\n"
        "  - name: build\n"
        "    steps:\n" + textwrap.indent(textwrap.dedent(steps_yaml), "      ")
    )


def _names(steps_yaml: str) -> list[str]:
    stages = compile_to_build_graph(parse_yaml_pipeline(_pipeline(steps_yaml)))
    return [step["name"] for step in stages[0]["steps"]]


def _name(steps_yaml: str) -> str:
    return _names(steps_yaml)[0]


def test_a_written_name_is_kept_exactly():
    assert _name("""
        - name: Build the backend
          run: make backend
    """) == "Build the backend"


def test_a_plain_string_step_is_labelled_by_its_command():
    assert _name('- "npm test"\n') == "npm test"


def test_a_run_step_is_labelled_by_its_first_command_line():
    assert _name("""
        - run: |

            npm ci
            npm run build
    """) == "npm ci"


def test_a_long_label_is_cut_at_60_characters():
    label = _name(f'- run: "echo {"x" * 100}"\n')
    assert len(label) == 60 and label.endswith("…")


def test_placeholders_show_the_variable_name_and_never_its_value():
    assert _name("""
        - ssh_exec:
            host: ${{ env.ENV_STAGING_APPS_1 }}
            user: slnee
            password: ${{ secrets.STAGING_SSH_PASSWORD }}
            commands: ["uptime"]
    """) == "SSH slnee@ENV_STAGING_APPS_1"


@pytest.mark.parametrize(
    ("steps_yaml", "label"),
    [
        ("""
            - git_clone:
                repo: "https://git.example.com/deom/admin_console.git"
                branch: develop
         """, "Clone admin_console (develop)"),
        ("""
            - git_clone:
                repo: "https://git.example.com/deom/admin_console.git"
         """, "Clone admin_console"),
        ("- git_pull:\n", "Git pull"),
        ("- git_push:\n    branch: main\n", "Git push (main)"),
        ("""
            - docker_build:
                context: "."
                tags: ["registry.example.com/console/backend:latest"]
         """, "Build backend:latest"),
        ("""
            - docker_build:
                tags: ["backend:latest", "backend:1.2"]
         """, "Build backend:latest +1 more"),
        ("""
            - docker_push:
                tags:
                  - "registry.example.com/console/backend:latest"
                  - "registry.example.com/console/worker:latest"
                  - "registry.example.com/console/frontend:latest"
         """, "Push backend:latest +2 more"),
        ("- docker_push:\n    image: registry.example.com/app:1\n", "Push app:1"),
        ("""
            - docker_login:
                registry: registry.example.com
                username: deploy
                password: ${{ secrets.TOKEN }}
         """, "Log in to registry.example.com"),
        ("- docker_login:\n    username: deploy\n", "Docker login"),
        ("- ssh_exec:\n    host: 10.0.0.5\n    commands: [uptime]\n", "SSH 10.0.0.5"),
        ("""
            - kube_apply:
                kubeconfig: ${{ secrets.KUBECONFIG }}
                manifests: [k8s/deployment.yaml, k8s/service.yaml]
         """, "Apply k8s/deployment.yaml +1 more"),
        ("- http_request:\n    url: https://example.com/hook\n", "POST https://example.com/hook"),
        ("""
            - http_request:
                method: get
                url: ${{ secrets.HEALTH_URL }}
         """, "GET HEALTH_URL"),
        ("- write_file:\n    path: .env\n    content: A=1\n", "Write .env"),
        ("- copy_files:\n    source: dist\n    destination: /srv/app\n", "Copy dist to /srv/app"),
        ("- delete_files:\n    path: tmp\n", "Delete tmp"),
        ("- delete_files:\n    paths: [a, b, c]\n", "Delete a +2 more"),
        ("- notify:\n    channel: telegram\n    message: hi\n", "Notify telegram"),
        ("- trigger_pipeline:\n    pipeline: deploy-prod\n", "Trigger deploy-prod"),
        ("- wait_input:\n", "Wait for approval"),
        ("- wait_webhook:\n", "Wait for webhook"),
        ("""
            - ai_agent:
                api_key: ${{ secrets.KEY }}
                prompt: |
                  Review the diff
                  and report problems
         """, "AI agent: Review the diff"),
    ],
)
def test_each_step_type_gets_a_label_from_its_settings(steps_yaml, label):
    assert _name(steps_yaml) == label


def test_repeated_labels_in_a_stage_are_numbered():
    assert _names("""
        - "make"
        - "make"
        - "make"
    """) == ["make", "make (2)", "make (3)"]


def test_a_label_does_not_take_a_written_name():
    assert _names("""
        - name: make
          run: echo first
        - "make"
    """) == ["make", "make (2)"]


def test_the_same_label_in_different_stages_is_not_numbered():
    yaml_doc = (
        "stages:\n"
        "  - name: one\n"
        "    steps: [\"make\"]\n"
        "  - name: two\n"
        "    steps: [\"make\"]\n"
    )
    stages = compile_to_build_graph(parse_yaml_pipeline(yaml_doc))
    assert [s["steps"][0]["name"] for s in stages] == ["make", "make"]


def test_repeated_written_names_are_kept_as_written():
    assert _names("""
        - name: deploy
          run: echo a
        - name: deploy
          run: echo b
    """) == ["deploy", "deploy"]
    assert validate_pipeline(_pipeline("""
        - name: deploy
          run: echo a
        - name: deploy
          run: echo b
    """)) == []


@pytest.mark.parametrize(
    "name_yaml",
    ['""', '"   "', "123", "[a, b]", "null", '"' + "x" * 256 + '"'],
)
def test_a_name_must_be_a_non_empty_string_of_at_most_255_characters(name_yaml):
    errors = validate_pipeline(_pipeline(f"- name: {name_yaml}\n  run: echo hi\n"))
    assert any("'name' must be" in e for e in errors), errors


def test_a_name_of_255_characters_is_valid():
    assert validate_pipeline(_pipeline(f'- name: "{"x" * 255}"\n  run: echo hi\n')) == []
