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
