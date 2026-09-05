locals {
  github_repository_id       = "1304812158"
  github_repository_owner_id = "133175353"
  github_shared_ref          = "refs/heads/exp/execution-control-plane-v1"

  bootstrap_services = toset([
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    "storage.googleapis.com",
  ])

  deployment_project_roles = toset([
    "roles/artifactregistry.admin",
    "roles/cloudsql.admin",
    "roles/compute.networkAdmin",
    "roles/iam.serviceAccountAdmin",
    "roles/iam.serviceAccountUser",
    "roles/run.admin",
    "roles/secretmanager.admin",
    "roles/servicenetworking.networksAdmin",
    "roles/serviceusage.serviceUsageAdmin",
  ])
}

resource "google_project_service" "bootstrap" {
  for_each = local.bootstrap_services

  project                    = var.project_id
  service                    = each.value
  disable_on_destroy         = false
  disable_dependent_services = false
}

resource "google_storage_bucket" "terraform_state" {
  project  = var.project_id
  name     = var.state_bucket_name
  location = var.state_bucket_location

  force_destroy               = false
  public_access_prevention    = "enforced"
  uniform_bucket_level_access = true

  versioning {
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.bootstrap["storage.googleapis.com"]]
}

resource "google_service_account" "terraform_deployer" {
  project      = var.project_id
  account_id   = var.deployment_service_account_id
  display_name = "Hao Runtime v2 Terraform deployer"

  depends_on = [google_project_service.bootstrap["iam.googleapis.com"]]
}

resource "google_project_iam_member" "terraform_deployer" {
  for_each = local.deployment_project_roles

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.terraform_deployer.email}"
}

resource "google_storage_bucket_iam_member" "terraform_state" {
  bucket = google_storage_bucket.terraform_state.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.terraform_deployer.email}"
}

resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = var.workload_identity_pool_id
  display_name              = "Hao Runtime v2 GitHub Actions"
  description               = "Federated GitHub Actions identity for the Runtime v2 shared EXP deployment branch."

  depends_on = [google_project_service.bootstrap["iam.googleapis.com"]]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = var.workload_identity_provider_id
  display_name                       = "Runtime v2 shared EXP"
  description                        = "Trusts only the immutable Hao Runtime v2 GitHub repository identity on the shared EXP branch."

  attribute_mapping = {
    "google.subject"                = "assertion.sub"
    "attribute.repository_id"       = "assertion.repository_id"
    "attribute.repository_owner_id" = "assertion.repository_owner_id"
    "attribute.ref"                 = "assertion.ref"
  }

  attribute_condition = join(" && ", [
    "assertion.repository_id == '${local.github_repository_id}'",
    "assertion.repository_owner_id == '${local.github_repository_owner_id}'",
    "assertion.ref == '${local.github_shared_ref}'",
  ])

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }

  depends_on = [
    google_project_service.bootstrap["iam.googleapis.com"],
    google_project_service.bootstrap["sts.googleapis.com"],
  ]
}

resource "google_service_account_iam_member" "github_wif" {
  service_account_id = google_service_account.terraform_deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository_id/${local.github_repository_id}"

  depends_on = [google_iam_workload_identity_pool_provider.github]
}
