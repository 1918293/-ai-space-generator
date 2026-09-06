output "artifact_repository" {
  description = "Artifact Registry repository resource name."
  value       = google_artifact_registry_repository.runtime.name
}

output "runtime_network" {
  description = "Runtime VPC network ID."
  value       = google_compute_network.runtime.id
}

output "cloud_sql_instance_connection_name" {
  description = "Cloud SQL connection name; not a credential."
  value       = google_sql_database_instance.runtime.connection_name
}

output "cloud_sql_private_ip" {
  description = "Authoritative private IP used by the current direct psycopg Runtime database URL."
  value       = google_sql_database_instance.runtime.private_ip_address
}

output "migration_service_account" {
  value = google_service_account.migration.email
}

output "migration_job_name" {
  value = var.enable_runtime_migration ? google_cloud_run_v2_job.migration[0].name : null
}

output "api_service_account" {
  value = google_service_account.api.email
}

output "worker_service_account" {
  value = google_service_account.worker.email
}

output "api_service_uri" {
  value = var.enable_runtime_workloads ? google_cloud_run_v2_service.api[0].uri : null
}

output "worker_pool_name" {
  description = "Legacy single-pool Worker name. Null when explicit rainbow worker_versions are active."
  value = (
    var.enable_runtime_workloads && !local.worker_rainbow_enabled
    ? google_cloud_run_v2_worker_pool.worker[0].name
    : null
  )
}

output "api_candidate_revision" {
  description = "Deterministic API revision identity for the current release."
  value       = local.api_candidate_revision

  precondition {
    condition     = !var.enable_runtime_workloads || !var.initial_runtime_release || !var.allow_public_mcp_invoker
    error_message = "The explicitly gated first Runtime workload release must keep the public Cloud Run invoker disabled until the initial API and Worker compute are verified."
  }
}

output "worker_candidate_revision" {
  description = "Legacy single-pool Worker revision identity. Null when rainbow worker_versions are active."
  value       = local.worker_candidate_revision
}

output "external_bootstrap_required" {
  description = "Explicitly records prerequisites Terraform intentionally does not materialize."
  value = [
    "Secret Manager numeric secret versions with real values",
    "least-privilege Cloud SQL database user/password matching the database-url secret",
    "immutable Runtime image publication and Google-Built OTel Collector image digest selection",
    "numeric Secret Manager version containing the OTel Collector config",
    "migration-only database credential plus successful in-VPC migration Job/schema readback",
    "Temporal Cloud namespace/API key",
    "OAuth IdP/JWKS/subject",
    "optional custom domain/DNS/TLS and public Cloud Run invoker authorization; the stable run.app URL is sufficient for the initial candidate",
    "Workspace file sharing for Runtime service identity",
    "remote Terraform state/executor decision",
  ]
}
