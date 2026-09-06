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
  value = var.enable_runtime_workloads ? google_cloud_run_v2_worker_pool.worker[0].name : null
}

output "api_candidate_revision" {
  description = "Deterministic API revision identity for the current release."
  value       = local.api_candidate_revision
}

output "worker_candidate_revision" {
  description = "Deterministic Worker Pool revision identity for the current release."
  value       = local.worker_candidate_revision
}

output "external_bootstrap_required" {
  description = "Explicitly records prerequisites Terraform intentionally does not materialize."
  value = [
    "Secret Manager numeric secret versions with real values",
    "least-privilege Cloud SQL database user/password matching the database-url secret",
    "immutable Runtime and OTel Collector image publication",
    "Temporal Cloud namespace/API key",
    "OAuth IdP/JWKS/subject",
    "DNS/TLS and optional public Cloud Run invoker authorization",
    "Workspace file sharing for Runtime service identity",
    "remote Terraform state/executor decision",
  ]
}
