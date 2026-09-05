locals {
  required_services = toset([
    "artifactregistry.googleapis.com",
    "compute.googleapis.com",
    "iam.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "servicenetworking.googleapis.com",
    "sqladmin.googleapis.com",
  ])

  common_env = {
    HAO_RUNTIME_ENV                = "production"
    HAO_RUNTIME_REGION             = var.region
    HAO_RELEASE_ID                 = var.release_id
    HAO_DEPLOYMENT_ID              = var.deployment_id
    HAO_DATABASE_SCHEMA_VERSION    = "3"
    HAO_DATABASE_RPO_SECONDS       = "300"
    HAO_DATABASE_RTO_SECONDS       = "3600"
    HAO_WORKER_INSTANCE_COUNT      = tostring(var.worker_instance_count)
    HAO_GRACEFUL_SHUTDOWN_SECONDS  = "8"
    HAO_TEMPORAL_ENDPOINT          = var.temporal_endpoint
    HAO_TEMPORAL_NAMESPACE         = var.temporal_namespace
    HAO_TEMPORAL_TASK_QUEUE        = var.temporal_task_queue
    HAO_TEMPORAL_WORKER_VERSION    = var.temporal_worker_version
    HAO_INITIAL_MODE               = "EXP"
    HAO_INITIAL_TASK               = var.initial_task
    HAO_PUBLIC_MCP_URL             = var.public_mcp_url
    HAO_MCP_ALLOWED_HOSTS          = var.mcp_allowed_hosts
    HAO_MCP_ALLOWED_ORIGINS        = var.mcp_allowed_origins
    HAO_MCP_REQUEST_STATE_AUDIENCE = var.mcp_request_state_audience
    HAO_OAUTH_ISSUER_URL           = var.oauth_issuer_url
    HAO_OAUTH_RESOURCE_URL         = var.public_mcp_url
    HAO_OAUTH_AUDIENCE             = var.public_mcp_url
    HAO_OAUTH_JWKS_URL             = var.oauth_jwks_url
    HAO_EXPECTED_SUBJECT           = var.expected_hao_subject
    HAO_ATTESTATION_KEY_ID         = var.attestation_key_id
    HAO_OTEL_ENDPOINT              = "http://127.0.0.1:4318"
    HAO_SHEETS_TARGETS_JSON        = var.sheets_targets_json
    HAO_TASK_POLICIES_JSON         = var.task_policies_json
    HAO_PARENT_TASK_PLANS_JSON     = var.parent_task_plans_json
  }

  secret_version_paths = {
    temporal    = "${google_secret_manager_secret.temporal_api_key.id}/versions/${var.temporal_api_key_version}"
    database    = "${google_secret_manager_secret.database_url.id}/versions/${var.database_url_secret_version}"
    attestation = "${google_secret_manager_secret.attestation.id}/versions/${var.attestation_secret_version}"
    mcp_state   = "${google_secret_manager_secret.mcp_request_state.id}/versions/${var.mcp_request_state_keys_version}"
  }

  api_secret_bindings = merge(
    {
      HAO_TEMPORAL_API_KEY       = local.secret_version_paths.temporal
      HAO_DATABASE_URL           = local.secret_version_paths.database
      HAO_ATTESTATION_SECRET     = local.secret_version_paths.attestation
      HAO_MCP_REQUEST_STATE_KEYS = local.secret_version_paths.mcp_state
    },
    var.previous_attestation_keys_secret_version == null ? {} : {
      HAO_ATTESTATION_PREVIOUS_KEYS_JSON = "${google_secret_manager_secret.attestation_previous.id}/versions/${var.previous_attestation_keys_secret_version}"
    }
  )

  worker_secret_bindings = {
    HAO_TEMPORAL_API_KEY = local.secret_version_paths.temporal
    HAO_DATABASE_URL     = local.secret_version_paths.database
  }
}

resource "google_project_service" "required" {
  for_each = local.required_services

  project                    = var.project_id
  service                    = each.value
  disable_on_destroy         = false
  disable_dependent_services = false
}

resource "google_compute_network" "runtime" {
  name                    = "${var.name_prefix}-network"
  project                 = var.project_id
  auto_create_subnetworks = false

  depends_on = [google_project_service.required["compute.googleapis.com"]]
}

resource "google_compute_subnetwork" "runtime" {
  name          = "${var.name_prefix}-subnet"
  project       = var.project_id
  region        = var.region
  network       = google_compute_network.runtime.id
  ip_cidr_range = var.network_cidr
}

resource "google_compute_global_address" "private_services" {
  name          = "${var.name_prefix}-private-services"
  project       = var.project_id
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = var.private_service_prefix_length
  network       = google_compute_network.runtime.id
}

resource "google_service_networking_connection" "private_services" {
  network                 = google_compute_network.runtime.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_services.name]

  depends_on = [google_project_service.required["servicenetworking.googleapis.com"]]
}

resource "google_artifact_registry_repository" "runtime" {
  project       = var.project_id
  location      = var.region
  repository_id = "${var.name_prefix}-containers"
  description   = "Runtime v2 immutable container images"
  format        = "DOCKER"

  docker_config {
    immutable_tags = true
  }

  depends_on = [google_project_service.required["artifactregistry.googleapis.com"]]
}

resource "google_service_account" "api" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-api"
  display_name = "Hao Runtime v2 API"
}

resource "google_service_account" "worker" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-worker"
  display_name = "Hao Runtime v2 Worker"
}

resource "google_secret_manager_secret" "temporal_api_key" {
  project   = var.project_id
  secret_id = "${var.name_prefix}-temporal-api-key"

  replication {
    auto {}
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.required["secretmanager.googleapis.com"]]
}

resource "google_secret_manager_secret" "database_url" {
  project   = var.project_id
  secret_id = "${var.name_prefix}-database-url"

  replication {
    auto {}
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.required["secretmanager.googleapis.com"]]
}

resource "google_secret_manager_secret" "attestation" {
  project   = var.project_id
  secret_id = "${var.name_prefix}-attestation"

  replication {
    auto {}
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.required["secretmanager.googleapis.com"]]
}

resource "google_secret_manager_secret" "mcp_request_state" {
  project   = var.project_id
  secret_id = "${var.name_prefix}-mcp-request-state"

  replication {
    auto {}
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.required["secretmanager.googleapis.com"]]
}

resource "google_secret_manager_secret" "attestation_previous" {
  project   = var.project_id
  secret_id = "${var.name_prefix}-attestation-previous"

  replication {
    auto {}
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.required["secretmanager.googleapis.com"]]
}

locals {
  api_secret_access = {
    temporal    = google_secret_manager_secret.temporal_api_key.id
    database    = google_secret_manager_secret.database_url.id
    attestation = google_secret_manager_secret.attestation.id
    mcp_state   = google_secret_manager_secret.mcp_request_state.id
  }

  worker_secret_access = {
    temporal = google_secret_manager_secret.temporal_api_key.id
    database = google_secret_manager_secret.database_url.id
  }
}

resource "google_secret_manager_secret_iam_member" "api" {
  for_each = local.api_secret_access

  project   = var.project_id
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

resource "google_secret_manager_secret_iam_member" "api_previous" {
  count = var.previous_attestation_keys_secret_version == null ? 0 : 1

  project   = var.project_id
  secret_id = google_secret_manager_secret.attestation_previous.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

resource "google_secret_manager_secret_iam_member" "worker" {
  for_each = local.worker_secret_access

  project   = var.project_id
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_sql_database_instance" "runtime" {
  project          = var.project_id
  name             = "${var.name_prefix}-postgres"
  region           = var.region
  database_version = var.cloud_sql_database_version

  deletion_protection = true

  settings {
    tier              = var.cloud_sql_tier
    edition           = var.cloud_sql_edition
    availability_type = var.cloud_sql_availability_type

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = var.cloud_sql_transaction_log_retention_days
    }

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.runtime.id
    }
  }

  lifecycle {
    prevent_destroy = true

    precondition {
      condition = !(
        var.cloud_sql_edition == "ENTERPRISE_PLUS" &&
        (
          startswith(var.cloud_sql_tier, "db-custom-") ||
          contains(["db-f1-micro", "db-g1-small"], var.cloud_sql_tier)
        )
      )
      error_message = "Cloud SQL ENTERPRISE_PLUS cannot use Enterprise shared-core or db-custom tiers. Select ENTERPRISE for db-custom/db-f1/db-g1 tiers or choose an Enterprise Plus-compatible predefined tier."
    }
  }

  depends_on = [
    google_project_service.required["sqladmin.googleapis.com"],
    google_service_networking_connection.private_services,
  ]
}

resource "google_sql_database" "runtime" {
  project  = var.project_id
  name     = "runtime"
  instance = google_sql_database_instance.runtime.name
}
