"""Validation and compilation tests for the kube_apply step type."""

from app.services.pipeline_compiler import (
    compile_to_build_graph,
    parse_yaml_pipeline,
    validate_pipeline,
)


def _pipeline(step_block: str) -> str:
    """Wrap a YAML step block (indented 6 spaces) in a minimal pipeline."""
    return (
        "version: 1\n"
        "name: test\n"
        "stages:\n"
        "  - name: deploy\n"
        "    steps:\n" + step_block
    )


VALID_FULL = _pipeline(
    "      - name: deploy to prod\n"
    "        kube_apply:\n"
    "          kubeconfig: ${{ secrets.PROD_KUBECONFIG }}\n"
    "          manifests:\n"
    "            - k8s/deployment.yaml\n"
    "            - k8s/service.yaml\n"
    "          namespace: production\n"
    "          context: prod-cluster\n"
    "          timeout: 600\n"
)


def test_valid_full_config_passes():
    assert validate_pipeline(VALID_FULL) == []


def test_valid_minimal_config_passes():
    yaml_doc = _pipeline(
        "      - kube_apply:\n"
        "          kubeconfig: ${{ secrets.KUBECONFIG }}\n"
        "          manifests:\n"
        "            - k8s/\n"
    )
    assert validate_pipeline(yaml_doc) == []


def test_missing_kubeconfig_fails():
    yaml_doc = _pipeline(
        "      - kube_apply:\n"
        "          manifests:\n"
        "            - k8s/deployment.yaml\n"
    )
    errors = validate_pipeline(yaml_doc)
    assert any("requires 'kubeconfig'" in e for e in errors)


def test_missing_manifests_fails():
    yaml_doc = _pipeline(
        "      - kube_apply:\n"
        "          kubeconfig: ${{ secrets.KUBECONFIG }}\n"
    )
    errors = validate_pipeline(yaml_doc)
    assert any("'manifests'" in e for e in errors)


def test_empty_manifests_fails():
    yaml_doc = _pipeline(
        "      - kube_apply:\n"
        "          kubeconfig: ${{ secrets.KUBECONFIG }}\n"
        "          manifests: []\n"
    )
    errors = validate_pipeline(yaml_doc)
    assert any("'manifests'" in e for e in errors)


def test_manifests_must_be_a_list():
    yaml_doc = _pipeline(
        "      - kube_apply:\n"
        "          kubeconfig: ${{ secrets.KUBECONFIG }}\n"
        "          manifests: k8s/deployment.yaml\n"
    )
    errors = validate_pipeline(yaml_doc)
    assert any("must be a list" in e for e in errors)


def test_manifest_entries_must_be_nonempty_strings():
    yaml_doc = _pipeline(
        "      - kube_apply:\n"
        "          kubeconfig: ${{ secrets.KUBECONFIG }}\n"
        "          manifests:\n"
        "            - 42\n"
    )
    errors = validate_pipeline(yaml_doc)
    assert any("non-empty strings" in e for e in errors)


def test_nonpositive_timeout_fails():
    yaml_doc = _pipeline(
        "      - kube_apply:\n"
        "          kubeconfig: ${{ secrets.KUBECONFIG }}\n"
        "          manifests:\n"
        "            - k8s/deployment.yaml\n"
        "          timeout: 0\n"
    )
    errors = validate_pipeline(yaml_doc)
    assert any("timeout must be a positive number" in e for e in errors)


def test_non_numeric_timeout_fails():
    yaml_doc = _pipeline(
        "      - kube_apply:\n"
        "          kubeconfig: ${{ secrets.KUBECONFIG }}\n"
        "          manifests:\n"
        "            - k8s/deployment.yaml\n"
        "          timeout: soon\n"
    )
    errors = validate_pipeline(yaml_doc)
    assert any("timeout must be a positive number" in e for e in errors)


def test_bool_timeout_fails():
    yaml_doc = _pipeline(
        "      - kube_apply:\n"
        "          kubeconfig: ${{ secrets.KUBECONFIG }}\n"
        "          manifests:\n"
        "            - k8s/deployment.yaml\n"
        "          timeout: true\n"
    )
    errors = validate_pipeline(yaml_doc)
    assert any("timeout must be a positive number" in e for e in errors)


def test_float_timeout_passes():
    yaml_doc = _pipeline(
        "      - kube_apply:\n"
        "          kubeconfig: ${{ secrets.KUBECONFIG }}\n"
        "          manifests:\n"
        "            - k8s/deployment.yaml\n"
        "          timeout: 30.5\n"
    )
    assert validate_pipeline(yaml_doc) == []


def test_kube_apply_must_be_a_mapping():
    yaml_doc = _pipeline("      - kube_apply: just-a-string\n")
    errors = validate_pipeline(yaml_doc)
    assert any("must be a mapping" in e for e in errors)


def test_compiles_to_kube_apply_step():
    stages = compile_to_build_graph(parse_yaml_pipeline(VALID_FULL))
    step = stages[0]["steps"][0]
    assert step["step_type"] == "kube_apply"
    assert step["name"] == "deploy to prod"
    assert step["config"]["kubeconfig"] == "${{ secrets.PROD_KUBECONFIG }}"
    assert step["config"]["manifests"] == ["k8s/deployment.yaml", "k8s/service.yaml"]
    assert step["config"]["namespace"] == "production"
    assert step["config"]["context"] == "prod-cluster"
    assert step["config"]["timeout"] == 600
