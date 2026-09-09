mock_provider "google" {}
mock_provider "google-beta" {}

variables {
  project_id                           = "hao-runtime-v2-test"
  region                               = "asia-east1"
  runtime_image                        = "example.invalid/runtime@sha256:0000000000000000000000000000000000000000000000000000000000000000"
  otel_collector_image                 = "example.invalid/otel@sha256:1111111111111111111111111111111111111111111111111111111111111111"
  otel_collector_config_secret_version = "1"
  release_id                           = "api-release"
  deployment_id                        = "test-deployment"
  temporal_worker_version              = "legacy-required-input"
  public_mcp_url                       = "https://runtime.example.invalid/mcp"
  mcp_allowed_hosts                    = "runtime.example.invalid"
  oauth_issuer_url                     = "https://issuer.example.invalid/"
  oauth_jwks_url                       = "https://issuer.example.invalid/.well-known/jwks.json"
  expected_hao_subject                 = "test-hao-subject"
  attestation_key_id                   = "test-key-1"
  initial_task                         = "Rainbow Worker contract test"
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
  initial_runtime_release              = true
  worker_versions = {
    "build-v1" = {
      release_id                   = "worker-release-v1"
      image                        = "example.invalid/runtime@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      instance_count               = 1
      database_min_schema_version  = 3
      storage_compatibility_epochs = [1]
    }
    "build-v2" = {
      release_id                   = "worker-release-v2"
      image                        = "example.invalid/runtime@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
      instance_count               = 1
      database_min_schema_version  = 3
      storage_compatibility_epochs = [1]
    }
    "build-v3" = {
      release_id                   = "worker-release-v3"
      image                        = "example.invalid/runtime@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      instance_count               = 0
      database_min_schema_version  = 3
      storage_compatibility_epochs = [1]
    }
  }
}

run "three_versions_coexist" {
  command = plan

  assert {
    condition     = length(google_cloud_run_v2_worker_pool.worker_version) == 3
    error_message = "PINNED rainbow mode must preserve all declared non-retired Worker Versions."
  }

  assert {
    condition     = length(google_cloud_run_v2_worker_pool.worker) == 0
    error_message = "Legacy single-pool Worker must be disabled when explicit rainbow versions exist."
  }

  assert {
    condition = (
      output.worker_version_pool_names["build-v1"] == "hao-runtime-v2-w-87297ffe1d2b" &&
      output.worker_version_pool_names["build-v2"] == "hao-runtime-v2-w-314feb2a1ecb" &&
      output.worker_version_pool_names["build-v3"] == "hao-runtime-v2-w-8a2d02b15337"
    )
    error_message = "Worker Pool names must be deterministic, bounded hashes of immutable Temporal Build IDs."
  }

  assert {
    condition     = google_cloud_run_v2_worker_pool.worker_version["build-v1"].scaling[0].manual_instance_count == 1
    error_message = "An active pinned old Worker Version must retain real polling capacity."
  }

  assert {
    condition     = google_cloud_run_v2_worker_pool.worker_version["build-v3"].scaling[0].manual_instance_count == 0
    error_message = "A safely drained version must be retainable with zero compute before explicit cleanup."
  }

  assert {
    condition     = output.worker_pool_name == null && output.worker_candidate_revision == null
    error_message = "Legacy single-pool outputs must be null in rainbow mode."
  }
}

run "new_version_does_not_replace_old_versions" {
  command = plan

  variables {
    initial_runtime_release = false
    api_stable_revision     = "hao-runtime-v2-api-000000000000"
    worker_versions = {
      "build-v1" = {
        release_id                   = "worker-release-v1"
        image                        = "example.invalid/runtime@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        instance_count               = 1
        database_min_schema_version  = 3
        storage_compatibility_epochs = [1]
      }
      "build-v2" = {
        release_id                   = "worker-release-v2"
        image                        = "example.invalid/runtime@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        instance_count               = 1
        database_min_schema_version  = 3
        storage_compatibility_epochs = [1]
      }
      "build-v3" = {
        release_id                   = "worker-release-v3"
        image                        = "example.invalid/runtime@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
        instance_count               = 1
        database_min_schema_version  = 3
        storage_compatibility_epochs = [1]
      }
      "build-v4" = {
        release_id                   = "worker-release-v4"
        image                        = "example.invalid/runtime@sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
        instance_count               = 1
        database_min_schema_version  = 3
        storage_compatibility_epochs = [1]
      }
    }
  }

  assert {
    condition = alltrue([
      contains(keys(google_cloud_run_v2_worker_pool.worker_version), "build-v1"),
      contains(keys(google_cloud_run_v2_worker_pool.worker_version), "build-v2"),
      contains(keys(google_cloud_run_v2_worker_pool.worker_version), "build-v3"),
      contains(keys(google_cloud_run_v2_worker_pool.worker_version), "build-v4"),
    ])
    error_message = "Adding a new Worker Version must not implicitly evict older non-retired PINNED versions."
  }
}

run "invalid_worker_version_fails_closed" {
  command = plan

  variables {
    worker_versions = {
      "bad-build" = {
        release_id                   = "worker-release-bad"
        image                        = "example.invalid/runtime:mutable"
        instance_count               = -1
        database_min_schema_version  = 0
        storage_compatibility_epochs = []
      }
    }
  }

  expect_failures = [var.worker_versions]
}
