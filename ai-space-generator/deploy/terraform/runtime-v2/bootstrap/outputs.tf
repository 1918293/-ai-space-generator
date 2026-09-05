output "state_bucket_name" {
  description = "Cloud Storage bucket used by the Runtime v2 GCS backend."
  value       = google_storage_bucket.terraform_state.name
}

output "state_prefix" {
  description = "Stable backend prefix for the Runtime v2 production state."
  value       = var.state_prefix
}

output "workload_identity_provider" {
  description = "Full Workload Identity Provider resource name for google-github-actions/auth."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "deployment_service_account" {
  description = "Service account impersonated by the authorized GitHub Actions deployment workflow."
  value       = google_service_account.terraform_deployer.email
}

output "trusted_github_ref" {
  description = "Only GitHub ref accepted by the bootstrap WIF provider."
  value       = local.github_shared_ref
}

output "deployment_project_roles" {
  description = "Project roles granted to the Runtime v2 Terraform deployment service account."
  value       = sort(tolist(local.deployment_project_roles))
}
