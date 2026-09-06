variable "worker_versions" {
  description = "Temporal PINNED rainbow worker versions. Each map key is the immutable Temporal Worker Build ID and owns one Cloud Run Worker Pool until the version is safely retired."
  type = map(object({
    release_id                   = string
    image                        = string
    instance_count               = number
    database_min_schema_version  = number
    storage_compatibility_epochs = set(number)
  }))
  default = {}

  validation {
    condition = alltrue([
      for build_id, version in var.worker_versions :
      length(trimspace(build_id)) > 0 &&
      length(trimspace(version.release_id)) > 0 &&
      can(regex("@sha256:[0-9a-fA-F]{64}$", version.image)) &&
      version.instance_count >= 0 &&
      version.database_min_schema_version >= 1 &&
      length(version.storage_compatibility_epochs) > 0 &&
      alltrue([for epoch in version.storage_compatibility_epochs : epoch >= 1])
    ])
    error_message = "Every worker_versions entry requires a non-empty immutable Build ID/release ID, digest-pinned image, non-negative capacity, positive minimum schema version, and positive storage compatibility epochs."
  }
}

locals {
  worker_rainbow_enabled = length(var.worker_versions) > 0
  worker_pool_names = {
    for build_id, _ in var.worker_versions :
    build_id => "${var.name_prefix}-w-${substr(sha256(build_id), 0, 12)}"
  }
  worker_revision_names = {
    for build_id, name in local.worker_pool_names :
    build_id => "${name}-r1"
  }
}

resource "google_cloud_run_v2_worker_pool" "worker_version" {
  provider = google-beta
  for_each = var.enable_runtime_workloads ? var.worker_versions : {}

  project             = var.project_id
  name                = local.worker_pool_names[each.key]
  location            = var.region
  deletion_protection = true

  scaling {
    scaling_mode          = "MANUAL"
    manual_instance_count = each.value.instance_count
  }

  template {
    revision        = local.worker_revision_names[each.key]
    service_account = google_service_account.worker.email

    volumes {
      name = "otel-config"
      secret {
        secret = google_secret_manager_secret.otel_collector_config.secret_id
        items {
          version = var.otel_collector_config_secret_version
          path    = "config.yaml"
        }
      }
    }

    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"

      network_interfaces {
        network    = google_compute_network.runtime.name
        subnetwork = google_compute_subnetwork.runtime.name
      }
    }

    containers {
      name       = "runtime-worker"
      image      = each.value.image
      depends_on = ["otel-collector"]

      resources {
        limits = {
          cpu    = var.worker_cpu
          memory = var.worker_memory
        }
      }

      dynamic "env" {
        for_each = merge(local.common_env, {
          HAO_RUNTIME_ROLE                  = "worker"
          HAO_RELEASE_ID                    = each.value.release_id
          HAO_TEMPORAL_WORKER_VERSION       = each.key
          HAO_WORKER_INSTANCE_COUNT         = tostring(each.value.instance_count)
          HAO_DATABASE_MIN_SCHEMA_VERSION   = tostring(each.value.database_min_schema_version)
          HAO_STORAGE_COMPATIBILITY_EPOCHS  = join(",", [for epoch in sort(tolist(each.value.storage_compatibility_epochs)) : tostring(epoch)])
          HAO_SECRET_BINDINGS_JSON          = jsonencode(local.worker_secret_bindings)
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
      args  = ["--config=/etc/otelcol-google/config.yaml"]

      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }

      volume_mounts {
        name       = "otel-config"
        mount_path = "/etc/otelcol-google"
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

  lifecycle {
    precondition {
      condition     = length(local.worker_pool_names[each.key]) <= 49
      error_message = "Deterministic rainbow Worker Pool name exceeds the Cloud Run 49-character limit."
    }
  }

  depends_on = [
    google_project_service.required["run.googleapis.com"],
    google_secret_manager_secret_iam_member.worker,
    google_sql_database.runtime,
  ]
}

output "worker_version_pool_names" {
  description = "Build-ID keyed Cloud Run Worker Pool names for Temporal PINNED rainbow versions."
  value = {
    for build_id, pool in google_cloud_run_v2_worker_pool.worker_version :
    build_id => pool.name
  }
}

output "worker_version_revision_names" {
  description = "Build-ID keyed deterministic Worker Pool revision names."
  value = {
    for build_id, _ in google_cloud_run_v2_worker_pool.worker_version :
    build_id => local.worker_revision_names[build_id]
  }
}
