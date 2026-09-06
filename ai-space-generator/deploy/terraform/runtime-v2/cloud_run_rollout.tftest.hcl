mock_provider "google" {}
mock_provider "google-beta" {}

variables {
  project_id                           = "hao-runtime-v2-test"
  region                               = "asia-east1"
  runtime_image                        = "example.invalid/runtime@sha256:0000000000000000000000000000000000000000000000000000000000000000"
  otel_collector_image                 = "example.invalid/otel@sha256:1111111111111111111111111111111111111111111111111111111111111111"
  otel_collector_config_secret_version = "1"
  release_id                           = "test-release"
  deployment_id                        = "test-deployment"
  temporal_worker_version              = "test-worker-v1"
  public_mcp_url                       = "https://runtime.example.invalid/mcp"
  mcp_allowed_hosts                    = "runtime.example.invalid"
  oauth_issuer_url                     = "https://issuer.example.invalid/"
  oauth_jwks_url                       = "https://issuer.example.invalid/.well-known/jwks.json"
  expected_hao_subject                 = "test-hao-subject"
  attestation_key_id                   = "test-key-1"
  initial_task                         = "Cloud Run revision-pinning contract test"
  sheets_targets_json                  = "{}"
  task_policies_json                   = "{}"
  parent_task_plans_json               = "{}"
  temporal_endpoint                    = "test.tmprl.cloud:7233"
  temporal_namespace                   = "test.namespace"
  temporal_api_key_version             = "1"
  attestation_secret_version           = "1"
  mcp_request_state_keys_version       = "1"
  database_url_secret_version          = "1"
  cloud_sql_edition                    = "ENTERPRISE"
  cloud_sql_tier                       = "db-custom-1-3840"
  cloud_sql_availability_type          = "ZONAL"
  enable_runtime_workloads             = true
  allow_public_mcp_invoker             = false
}

run "first_release" {
  command = plan
  variables {
    initial_runtime_release = true
  }
  assert {
    condition     = output.api_candidate_revision == "hao-runtime-v2-api-41c7250e092d"
    error_message = "API revision must be deterministic."
  }
  assert {
    condition     = output.worker_candidate_revision == "hao-runtime-v2-worker-41c7250e092d"
    error_message = "Worker revision must be deterministic."
  }
  assert {
    condition     = google_cloud_run_v2_service.api[0].traffic[0].type == "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST" && google_cloud_run_v2_service.api[0].traffic[0].percent == 100
    error_message = "Explicit first release must be the only latest=100 state."
  }
}

run "first_release_cannot_be_public" {
  command = plan
  variables {
    initial_runtime_release  = true
    allow_public_mcp_invoker = true
  }
  expect_failures = [output.api_candidate_revision]
}

run "pin_first_release" {
  command = plan
  variables {
    api_stable_revision    = "hao-runtime-v2-api-41c7250e092d"
    worker_stable_revision = "hao-runtime-v2-worker-41c7250e092d"
  }
  assert {
    condition     = length(google_cloud_run_v2_service.api[0].traffic) == 1 && google_cloud_run_v2_service.api[0].traffic[0].revision == "hao-runtime-v2-api-41c7250e092d" && google_cloud_run_v2_service.api[0].traffic[0].percent == 100
    error_message = "API stable revision must be exact-pinned."
  }
  assert {
    condition     = length(google_cloud_run_v2_worker_pool.worker[0].instance_splits) == 1 && google_cloud_run_v2_worker_pool.worker[0].instance_splits[0].revision == "hao-runtime-v2-worker-41c7250e092d" && google_cloud_run_v2_worker_pool.worker[0].instance_splits[0].percent == 100
    error_message = "Worker stable revision must be exact-pinned."
  }
}

run "later_candidate_zero" {
  command = plan
  variables {
    release_id              = "test-release-2"
    temporal_worker_version = "test-worker-v2"
    api_stable_revision     = "hao-runtime-v2-api-41c7250e092d"
    worker_stable_revision  = "hao-runtime-v2-worker-41c7250e092d"
  }
  assert {
    condition     = google_cloud_run_v2_service.api[0].traffic[0].revision == "hao-runtime-v2-api-41c7250e092d" && google_cloud_run_v2_service.api[0].traffic[0].percent == 100
    error_message = "Stable API must remain 100%."
  }
  assert {
    condition     = google_cloud_run_v2_service.api[0].traffic[1].revision == "hao-runtime-v2-api-b22833f0cfc1" && google_cloud_run_v2_service.api[0].traffic[1].percent == 0 && google_cloud_run_v2_service.api[0].traffic[1].tag == "candidate"
    error_message = "API candidate must start at 0% and be tagged."
  }
  assert {
    condition     = google_cloud_run_v2_worker_pool.worker[0].instance_splits[0].revision == "hao-runtime-v2-worker-41c7250e092d" && google_cloud_run_v2_worker_pool.worker[0].instance_splits[0].percent == 100
    error_message = "Stable Worker must remain 100%."
  }
  assert {
    condition     = google_cloud_run_v2_worker_pool.worker[0].instance_splits[1].revision == "hao-runtime-v2-worker-b22833f0cfc1" && google_cloud_run_v2_worker_pool.worker[0].instance_splits[1].percent == 0
    error_message = "Worker candidate must start at 0%."
  }
}

run "missing_stable_fails" {
  command         = plan
  expect_failures = [google_cloud_run_v2_service.api]
}
