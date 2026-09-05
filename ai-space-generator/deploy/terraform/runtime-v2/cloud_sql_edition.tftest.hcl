mock_provider "google" {}
mock_provider "google-beta" {}

variables {
  project_id = "hao-runtime-v2-test"
  region     = "asia-east1"

  runtime_image        = "example.invalid/runtime@sha256:0000000000000000000000000000000000000000000000000000000000000000"
  otel_collector_image = "example.invalid/otel@sha256:1111111111111111111111111111111111111111111111111111111111111111"
  otel_exporter_otlp_endpoint = "https://otel.example.invalid/v1/traces"

  release_id              = "test-release"
  deployment_id           = "test-deployment"
  temporal_worker_version = "test-worker-v1"

  public_mcp_url     = "https://runtime.example.invalid/mcp"
  mcp_allowed_hosts  = "runtime.example.invalid"
  oauth_issuer_url   = "https://issuer.example.invalid/"
  oauth_jwks_url     = "https://issuer.example.invalid/.well-known/jwks.json"
  expected_hao_subject = "test-hao-subject"
  attestation_key_id   = "test-key-1"
  initial_task          = "Terraform Cloud SQL edition contract test"

  sheets_targets_json     = "{}"
  task_policies_json      = "{}"
  parent_task_plans_json  = "{}"

  temporal_endpoint  = "test.tmprl.cloud:7233"
  temporal_namespace = "test.namespace"

  temporal_api_key_version       = "1"
  attestation_secret_version     = "1"
  mcp_request_state_keys_version = "1"
  database_url_secret_version    = "1"

  cloud_sql_edition          = "ENTERPRISE"
  cloud_sql_tier             = "db-custom-1-3840"
  cloud_sql_availability_type = "ZONAL"

  enable_runtime_workloads = false
  allow_public_mcp_invoker  = false
}

run "enterprise_custom_tier_allowed" {
  command = plan
}

run "enterprise_plus_rejects_enterprise_custom_tier" {
  command = plan

  variables {
    cloud_sql_edition = "ENTERPRISE_PLUS"
  }

  expect_failures = [google_sql_database_instance.runtime]
}
