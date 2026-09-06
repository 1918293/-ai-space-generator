mock_provider "google" {}
mock_provider "google-beta" {}

variables {
  project_id                  = "hao-runtime-v2-staged-input-test"
  cloud_sql_edition           = "ENTERPRISE"
  cloud_sql_tier              = "db-custom-1-3840"
  cloud_sql_availability_type = "ZONAL"
}

run "base_infrastructure_without_workload_inputs" {
  command = plan

  assert {
    condition     = length(google_cloud_run_v2_service.api) == 0 && length(google_cloud_run_v2_worker_pool.worker) == 0
    error_message = "Base infrastructure planning must keep Runtime API and Worker disabled."
  }

  assert {
    condition     = output.api_candidate_revision == null && output.worker_candidate_revision == null
    error_message = "Disabled workloads must not manufacture candidate revision identities."
  }
}

run "workload_enablement_requires_release_inputs" {
  command = plan
  variables { enable_runtime_workloads = true }
  expect_failures = [var.enable_runtime_workloads]
}


run "migration_enablement_requires_migration_inputs" {
  command = plan
  variables { enable_runtime_migration = true }
  expect_failures = [var.enable_runtime_migration]
}

run "migration_job_is_in_vpc_and_single_attempt" {
  command = plan
  variables {
    enable_runtime_migration              = true
    runtime_image                         = "example.invalid/runtime@sha256:0000000000000000000000000000000000000000000000000000000000000000"
    release_id                            = "migration-test-release"
    migration_database_url_secret_version = "1"
  }

  assert {
    condition     = length(google_cloud_run_v2_job.migration) == 1
    error_message = "Migration stage must create exactly one Cloud Run Job."
  }
  assert {
    condition     = google_cloud_run_v2_job.migration[0].template[0].template[0].max_retries == 0
    error_message = "Migration Job must not automatically retry consequential DDL."
  }
  assert {
    condition     = google_cloud_run_v2_job.migration[0].template[0].template[0].vpc_access[0].egress == "PRIVATE_RANGES_ONLY"
    error_message = "Migration Job must use private-range Direct VPC egress."
  }
}
