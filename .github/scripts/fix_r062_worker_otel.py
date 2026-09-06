from pathlib import Path

path = Path("ai-space-generator/deploy/terraform/runtime-v2/cloud_run.tf")
text = path.read_text()
old = '''    containers {
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
'''
new = '''    containers {
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
'''
if text.count(old) != 1:
    raise SystemExit(f"expected exactly one remaining legacy Worker OTel block; found {text.count(old)}")
path.write_text(text.replace(old, new, 1))
