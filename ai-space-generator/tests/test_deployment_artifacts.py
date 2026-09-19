from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_container_is_non_root_and_sigterm_aware():
    dockerfile = (ROOT / "Dockerfile.runtime").read_text()
    assert "USER hao-runtime" in dockerfile
    assert "STOPSIGNAL SIGTERM" in dockerfile
    assert 'CMD ["python", "-m", "src.runtime_deployment"]' in dockerfile


def test_otel_collector_is_loopback_bounded_and_has_no_payload_dump_exporter():
    config = (ROOT / "deploy" / "otel-collector.yaml").read_text()
    assert "endpoint: 127.0.0.1:4318" in config
    assert config.index("memory_limiter") < config.index("batch:")
    assert "limit_mib: 256" in config
    assert "queue_size: 2048" in config
    assert "max_elapsed_time: 60s" in config
    assert "debug:" not in config
    assert "logging:" not in config


def test_production_env_example_contains_only_secret_placeholders_and_numeric_binding_contract():
    example = (ROOT / "deploy" / "runtime-production.env.example").read_text()
    assert "HAO_TEMPORAL_API_KEY=<secret-manager-injected>" in example
    assert "HAO_ATTESTATION_SECRET=<secret-manager-injected>" in example
    assert "HAO_MCP_REQUEST_STATE_KEYS=<secret-manager-injected>" in example
    assert "/versions/latest" not in example
    assert '"HAO_TEMPORAL_API_KEY":"projects/<project>/secrets/temporal-api-key/versions/<n>"' in example


def test_runtime_reasoning_inputs_are_wired_through_api_deployment_contract():
    example = (ROOT / "deploy" / "runtime-production.env.example").read_text()
    main_tf = (ROOT / "deploy" / "terraform" / "runtime-v2" / "main.tf").read_text()
    cloud_run_tf = (ROOT / "deploy" / "terraform" / "runtime-v2" / "cloud_run.tf").read_text()
    variables_tf = (ROOT / "deploy" / "terraform" / "runtime-v2" / "variables.tf").read_text()

    contract = {
        "HAO_REASONING_MODEL": "reasoning_model",
        "HAO_CONTEXT_REASONING_ROUTES_JSON": "context_reasoning_routes_json",
        "HAO_CANONICAL_SEMANTIC_SOURCES_JSON": "canonical_semantic_sources_json",
        "HAO_ACTIVE_WORK_INDEX_SOURCE_JSON": "active_work_index_source_json",
    }
    workload_gate = variables_tf[variables_tf.index('variable "enable_runtime_workloads"'):]

    for env_key, variable_name in contract.items():
        assert env_key in example
        assert env_key in main_tf
        assert f"var.{variable_name}" in main_tf
        assert f'variable "{variable_name}"' in variables_tf
        assert f"var.{variable_name}," in workload_gate

    assert "merge(local.common_env, local.api_reasoning_env" in cloud_run_tf
    assert cloud_run_tf.count("local.api_reasoning_env") == 1
