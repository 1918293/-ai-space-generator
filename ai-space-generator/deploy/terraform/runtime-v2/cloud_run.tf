locals {
  runtime_revision_suffix   = substr(sha256(var.release_id), 0, 12)
  api_candidate_revision    = "${var.name_prefix}-api-${local.runtime_revision_suffix}"
  worker_candidate_revision = "${var.name_prefix}-worker-${local.runtime_revision_suffix}"
  rollout_contract_valid = var.initial_runtime_release ? (
    var.api_stable_revision == null && var.worker_stable_revision == null
    ) : (
    var.api_stable_revision != null && var.worker_stable_revision != null
  )
  stable_revision_names_valid = (
    (var.api_stable_revision == null ? true : startswith(var.api_stable_revision, "${var.name_prefix}-api-")) &&
    (var.worker_stable_revision == null ? true : startswith(var.worker_stable_revision, "${var.name_prefix}-worker-"))
  )
}

resource "google_cloud_run_v2_service" "api" {
  count = var.enable_runtime_workloads ? 1 : 0

  project             = var.project_id
  name                = "${var.name_prefix}-api"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = true

  lifecycle {
    precondition {
      condition     = local.rollout_contract_valid
      error_message = "First workload release requires initial_runtime_release=true with no stable revisions; every later release must pin exact API and Worker stable revisions."
    }
    precondition {
      condition     = local.stable_revision_names_valid
      error_message = "Stable revisions must belong to the configured Runtime API/Worker resources."
    }
    precondition {
      condition     = length(local.api_candidate_revision) <= 63 && length(local.worker_candidate_revision) <= 63
      error_message = "Deterministic Runtime revision names exceed the Cloud Run 63-character limit."
    }
  }

  scaling {
    min_instance_count = var.api_min_instances
    max_instance_count = var.api_max_instances
  }

  template {
    revision        = local.api_candidate_revision
    service_account = google_service_account.api.email

    scaling {
      min_instance_count = var.api_min_instances
      max_instance_count = var.api_max_instances
    }

    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"

      network_interfaces {
        network    = google_compute_network.runtime.name
        subnetwork = google_compute_subnetwork.runtime.name
      }
    }

    containers {
      name  = "runtime-api"
      image = var.runtime_image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = var.api_cpu
          memory = var.api_memory
        }
      }

      dynamic "env" {
        for_each = merge(local.common_env, {
          HAO_RUNTIME_ROLE         = "api"
          HAO_SECRET_BINDINGS_JSON = jsonencode(local.api_secret_bindings)
        })
        content {
          name  = env.key
          value = env.value
        }
      }

      env {
        name = "HAO_DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.database_url.secret_id
            version = var.database_url_secret_version
          }
        }
      }

      env {
        name = "HAO_TEMPORAL_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.temporal_api_key.secret_id
            version = var.temporal_api_key_version
          }
        }
      }

      env {
        name = "HAO_ATTESTATION_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.attestation.secret_id
            version = var.attestation_secret_version
          }
        }
      }

      env {
        name = "HAO_MCP_REQUEST_STATE_KEYS"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.mcp_request_state.secret_id
            version = var.mcp_request_state_keys_version
          }
        }
      }

      dynamic "env" {
        for_each = var.previous_attestation_keys_secret_version == null ? [] : [var.previous_attestation_keys_secret_version]
        content {
          name = "HAO_ATTESTATION_PREVIOUS_KEYS_JSON"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.attestation_previous.secret_id
              version = env.value
            }
          }
        }
      }

      startup_probe {
        timeout_seconds   = 2
        period_seconds    = 5
        failure_threshold = 12

        http_get {
          path = "/startupz"
          port = 8080
        }
      }

      readiness_probe {
        timeout_seconds   = 2
        period_seconds    = 5
        failure_threshold = 3
        success_threshold = 1

        http_get {
          path = "/readyz"
          port = 8080
        }
      }

      liveness_probe {
        timeout_seconds   = 2
        period_seconds    = 10
        failure_threshold = 3

        http_get {
          path = "/livez"
          port = 8080
        }
      }
    }

    containers {
      name  = "otel-collector"
      image = var.otel_collector_image

      env {
        name  = "OTEL_EXPORTER_OTLP_ENDPOINT"
        value = var.otel_exporter_otlp_endpoint
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      startup_probe {
        timeout_seconds   = 2
        period_seconds    = 5
        failure_threshold = 12

        http_get {
          path = "/"
          port = 13133
        }
      }
    }
  }

  dynamic "traffic" {
    for_each = var.api_stable_revision == null ? [1] : []
    content {
      type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
      percent = 100
    }
  }

  dynamic "traffic" {
    for_each = var.api_stable_revision == null ? [] : [var.api_stable_revision]
    content {
      type     = "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION"
      revision = traffic.value
      percent  = 100
    }
  }

  dynamic "traffic" {
    for_each = var.api_stable_revision != null && var.api_stable_revision != local.api_candidate_revision ? [local.api_candidate_revision] : []
    content {
      type     = "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION"
      revision = traffic.value
      percent  = 0
      tag      = "candidate"
    }
  }

  depends_on = [
    google_project_service.required["run.googleapis.com"],
    google_secret_manager_secret_iam_member.api,
    google_sql_database.runtime,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "public_invoker" {
  count = var.enable_runtime_workloads && var.allow_public_mcp_invoker ? 1 : 0

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.api[0].name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_worker_pool" "worker" {
  provider = google-beta
  count    = var.enable_runtime_workloads ? 1 : 0

  project             = var.project_id
  name                = "${var.name_prefix}-worker"
  location            = var.region
  deletion_protection = true

  scaling {
    scaling_mode          = "MANUAL"
    manual_instance_count = var.worker_instance_count
  }

  dynamic "instance_splits" {
    for_each = var.worker_stable_revision == null ? [1] : []
    content {
      type    = "INSTANCE_SPLIT_ALLOCATION_TYPE_LATEST"
      percent = 100
    }
  }

  dynamic "instance_splits" {
    for_each = var.worker_stable_revision == null ? [] : [var.worker_stable_revision]
    content {
      type     = "INSTANCE_SPLIT_ALLOCATION_TYPE_REVISION"
      revision = instance_splits.value
      percent  = 100
    }
  }

  dynamic "instance_splits" {
    for_each = var.worker_stable_revision != null && var.worker_stable_revision != local.worker_candidate_revision ? [local.worker_candidate_revision] : []
    content {
      type     = "INSTANCE_SPLIT_ALLOCATION_TYPE_REVISION"
      revision = instance_splits.value
      percent  = 0
    }
  }

  template {
    revision        = local.worker_candidate_revision
    service_account = google_service_account.worker.email

    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"

      network_interfaces {
        network    = google_compute_network.runtime.name
        subnetwork = google_compute_subnetwork.runtime.name
      }
    }

    containers {
      name  = "runtime-worker"
      image = var.runtime_image

      resources {
        limits = {
          cpu    = var.worker_cpu
          memory = var.worker_memory
        }
      }

      dynamic "env" {
        for_each = merge(local.common_env, {
          HAO_RUNTIME_ROLE         = "worker"
          HAO_SECRET_BINDINGS_JSON = jsonencode(local.worker_secret_bindings)
        })
        content {
          name  = env.key
          value = env.value
        }
      }

      env {
        name = "HAO_DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.database_url.secret_id
            version = var.database_url_secret_version
          }
        }
      }

      env {
        name = "HAO_TEMPORAL_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.temporal_api_key.secret_id
            version = var.temporal_api_key_version
          }
        }
      }
    }

    containers {
      name  = "otel-collector"
      image = var.otel_collector_image

      env {
        name  = "OTEL_EXPORTER_OTLP_ENDPOINT"
        value = var.otel_exporter_otlp_endpoint
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
    }
  }

  depends_on = [
    google_project_service.required["run.googleapis.com"],
    google_secret_manager_secret_iam_member.worker,
    google_sql_database.runtime,
  ]
}
