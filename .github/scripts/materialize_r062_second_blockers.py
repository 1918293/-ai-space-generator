from pathlib import Path

root = Path("ai-space-generator/deploy/terraform/runtime-v2")
variables_path = root / "variables.tf"
main_path = root / "main.tf"
cloud_run_path = root / "cloud_run.tf"
outputs_path = root / "outputs.tf"
staged_test_path = root / "staged_inputs.tftest.hcl"
rollout_test_path = root / "cloud_run_rollout.tftest.hcl"
cloud_sql_test_path = root / "cloud_sql_edition.tftest.hcl"
readme_path = root / "README.md"
otel_path = Path("ai-space-generator/deploy/otel-collector.yaml")
migrations_path = Path("ai-space-generator/src/runtime_migrations.py")
migration_test_path = Path("ai-space-generator/tests/test_runtime_migrations.py")
image_workflow_path = Path(".github/workflows/runtime-v2-image-publish.yml")
migration_workflow_path = Path(".github/workflows/runtime-v2-migration-execute.yml")


def variable_span(text: str, name: str) -> tuple[int, int, str]:
    marker = f'variable "{name}" {{'
    start = text.index(marker)
    next_start = text.find('\nvariable "', start + len(marker))
    end = len(text) if next_start == -1 else next_start + 1
    return start, end, text[start:end]


# variables.tf
text = variables_path.read_text()
start, end, _ = variable_span(text, "otel_exporter_otlp_endpoint")
text = text[:start] + text[end:]
old = 'description = "Immutable OTel Collector image that contains deploy/otel-collector.yaml."'
new = 'description = "Immutable Google-Built OpenTelemetry Collector image reference, pinned by OCI sha256 digest."'
if text.count(old) != 1:
    raise SystemExit("otel collector description mismatch")
text = text.replace(old, new, 1)

insert_before = 'variable "release_id" {'
addition = '''variable "otel_collector_config_secret_version" {
  type        = string
  default     = null
  description = "Existing numeric Secret Manager version containing deploy/otel-collector.yaml for the Google-Built Collector."

  validation {
    condition     = var.otel_collector_config_secret_version == null || can(regex("^[1-9][0-9]*$", var.otel_collector_config_secret_version))
    error_message = "otel_collector_config_secret_version must be a positive numeric version."
  }
}

'''
if insert_before not in text or "otel_collector_config_secret_version" in text:
    raise SystemExit("otel config variable insertion mismatch")
text = text.replace(insert_before, addition + insert_before, 1)

insert_before = 'variable "cloud_sql_edition" {'
addition = '''variable "migration_database_url_secret_version" {
  type        = string
  default     = null
  description = "Existing numeric Secret Manager version containing the migration-only PostgreSQL URL."

  validation {
    condition     = var.migration_database_url_secret_version == null || can(regex("^[1-9][0-9]*$", var.migration_database_url_secret_version))
    error_message = "migration_database_url_secret_version must be a positive numeric version."
  }
}

'''
if insert_before not in text or "migration_database_url_secret_version" in text:
    raise SystemExit("migration secret variable insertion mismatch")
text = text.replace(insert_before, addition + insert_before, 1)

marker = 'variable "enable_runtime_workloads" {'
addition = '''variable "enable_runtime_migration" {
  type        = bool
  description = "Create the in-VPC migration Job only after an immutable Runtime image and migration-only database credential exist."
  default     = false

  validation {
    condition = !var.enable_runtime_migration || alltrue([
      for value in [
        var.runtime_image,
        var.release_id,
        var.migration_database_url_secret_version,
      ] : try(length(trimspace(value)) > 0, false)
    ])
    error_message = "enable_runtime_migration=true requires runtime_image, release_id, and migration_database_url_secret_version."
  }
}

'''
if marker not in text or "enable_runtime_migration" in text:
    raise SystemExit("migration gate insertion mismatch")
text = text.replace(marker, addition + marker, 1)
if text.count("        var.otel_exporter_otlp_endpoint,\n") != 1:
    raise SystemExit("workload gate old OTel endpoint ref mismatch")
text = text.replace(
    "        var.otel_exporter_otlp_endpoint,\n",
    "        var.otel_collector_config_secret_version,\n",
    1,
)
variables_path.write_text(text)

# main.tf
main = main_path.read_text()
if '    "telemetry.googleapis.com",' in main:
    raise SystemExit("telemetry API already present unexpectedly")
main = main.replace(
    '    "sqladmin.googleapis.com",\n',
    '    "sqladmin.googleapis.com",\n    "telemetry.googleapis.com",\n',
    1,
)
worker_sa = '''resource "google_service_account" "worker" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-worker"
  display_name = "Hao Runtime v2 Worker"
}
'''
if main.count(worker_sa) != 1:
    raise SystemExit("worker service account block mismatch")
main = main.replace(
    worker_sa,
    worker_sa
    + '''
resource "google_service_account" "migration" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-migrate"
  display_name = "Hao Runtime v2 Database Migration"
}
''',
    1,
)

temporal_secret = 'resource "google_secret_manager_secret" "temporal_api_key" {'
secret_blocks = '''resource "google_secret_manager_secret" "otel_collector_config" {
  project   = var.project_id
  secret_id = "${var.name_prefix}-otel-config"

  replication {
    auto {}
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.required["secretmanager.googleapis.com"]]
}

resource "google_secret_manager_secret" "migration_database_url" {
  project   = var.project_id
  secret_id = "${var.name_prefix}-migration-database-url"

  replication {
    auto {}
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.required["secretmanager.googleapis.com"]]
}

'''
if temporal_secret not in main or 'resource "google_secret_manager_secret" "otel_collector_config"' in main:
    raise SystemExit("new secret insertion mismatch")
main = main.replace(temporal_secret, secret_blocks + temporal_secret, 1)

for map_name in ("api_secret_access", "worker_secret_access"):
    map_marker = f"  {map_name} = {{\n"
    if main.count(map_marker) != 1:
        raise SystemExit(f"{map_name} mismatch")
    main = main.replace(
        map_marker,
        map_marker + "    otel_config = google_secret_manager_secret.otel_collector_config.id\n",
        1,
    )

cloud_sql_marker = 'resource "google_sql_database_instance" "runtime" {'
iam_blocks = '''resource "google_secret_manager_secret_iam_member" "migration_database" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.migration_database_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.migration.email}"
}

locals {
  telemetry_service_accounts = {
    api    = google_service_account.api.email
    worker = google_service_account.worker.email
  }
}

resource "google_project_iam_member" "telemetry_writer" {
  for_each = local.telemetry_service_accounts
  project  = var.project_id
  role     = "roles/telemetry.writer"
  member   = "serviceAccount:${each.value}"
}

resource "google_project_iam_member" "telemetry_service_usage" {
  for_each = local.telemetry_service_accounts
  project  = var.project_id
  role     = "roles/serviceusage.serviceUsageConsumer"
  member   = "serviceAccount:${each.value}"
}

'''
if cloud_sql_marker not in main or 'resource "google_project_iam_member" "telemetry_writer"' in main:
    raise SystemExit("telemetry/migration IAM insertion mismatch")
main = main.replace(cloud_sql_marker, iam_blocks + cloud_sql_marker, 1)
main_path.write_text(main)

# cloud_run.tf
cloud = cloud_run_path.read_text()
api_sa = "    service_account = google_service_account.api.email\n"
worker_sa_line = "    service_account = google_service_account.worker.email\n"
volume = '''
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
'''
if cloud.count(api_sa) != 1 or cloud.count(worker_sa_line) != 1:
    raise SystemExit("Cloud Run service-account anchor mismatch")
cloud = cloud.replace(api_sa, api_sa + volume, 1)
cloud = cloud.replace(worker_sa_line, worker_sa_line + volume, 1)

cloud = cloud.replace(
    '    containers {\n      name  = "runtime-api"\n      image = var.runtime_image\n',
    '    containers {\n      name       = "runtime-api"\n      image      = var.runtime_image\n      depends_on = ["otel-collector"]\n',
    1,
)
cloud = cloud.replace(
    '    containers {\n      name  = "runtime-worker"\n      image = var.runtime_image\n',
    '    containers {\n      name       = "runtime-worker"\n      image      = var.runtime_image\n      depends_on = ["otel-collector"]\n',
    1,
)


def replace_otel_container(source: str) -> str:
    marker = '    containers {\n      name  = "otel-collector"'
    start = source.index(marker)
    depth = 0
    end = None
    for i in range(start, len(source)):
        ch = source[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        raise SystemExit("OTel container block end not found")
    block = '''    containers {
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
    }'''
    return source[:start] + block + source[end:]


cloud = replace_otel_container(cloud)
cloud = replace_otel_container(cloud)

migration_job = '''

resource "google_cloud_run_v2_job" "migration" {
  count = var.enable_runtime_migration ? 1 : 0

  project             = var.project_id
  name                = "${var.name_prefix}-migrate"
  location            = var.region
  deletion_protection = true

  template {
    template {
      service_account = google_service_account.migration.email
      max_retries     = 0
      timeout         = "600s"

      vpc_access {
        egress = "PRIVATE_RANGES_ONLY"
        network_interfaces {
          network    = google_compute_network.runtime.name
          subnetwork = google_compute_subnetwork.runtime.name
        }
      }

      containers {
        image   = var.runtime_image
        command = ["python", "-m", "src.runtime_migrations"]

        env {
          name = "HAO_DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.migration_database_url.secret_id
              version = var.migration_database_url_secret_version
            }
          }
        }

        env {
          name  = "HAO_RELEASE_ID"
          value = var.release_id
        }

        env {
          name  = "HAO_DATABASE_SCHEMA_VERSION"
          value = "3"
        }

        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"
          }
        }
      }
    }
  }

  depends_on = [
    google_project_service.required["run.googleapis.com"],
    google_secret_manager_secret_iam_member.migration_database,
    google_sql_database.runtime,
  ]
}
'''
if 'resource "google_cloud_run_v2_job" "migration"' in cloud:
    raise SystemExit("migration job already exists")
cloud_run_path.write_text(cloud.rstrip() + migration_job)

# outputs.tf
outputs = outputs_path.read_text()
anchor = '''output "cloud_sql_instance_connection_name" {
  description = "Cloud SQL connection name; not a credential."
  value       = google_sql_database_instance.runtime.connection_name
}
'''
if outputs.count(anchor) != 1:
    raise SystemExit("Cloud SQL output anchor mismatch")
outputs = outputs.replace(
    anchor,
    anchor
    + '''
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
''',
    1,
)
outputs = outputs.replace(
    '    "immutable Runtime and OTel Collector image publication",\n',
    '    "immutable Runtime image publication and Google-Built OTel Collector image digest selection",\n    "numeric Secret Manager version containing the OTel Collector config",\n    "migration-only database credential plus successful in-VPC migration Job/schema readback",\n',
    1,
)
outputs_path.write_text(outputs)

# OTel config
otel_path.write_text('''extensions:
  health_check:
    endpoint: 0.0.0.0:13133
  googleclientauth:

receivers:
  otlp:
    protocols:
      http:
        endpoint: 127.0.0.1:4318

processors:
  resourcedetection:
    detectors: [gcp]
    timeout: 10s
  resource/gcp_project_id:
    attributes:
      - action: insert
        key: gcp.project_id
        value: ${GOOGLE_CLOUD_PROJECT}
  memory_limiter:
    check_interval: 1s
    limit_mib: 256
    spike_limit_mib: 64
  batch:
    timeout: 5s
    send_batch_size: 512
    send_batch_max_size: 1024

exporters:
  otlp_grpc:
    endpoint: telemetry.googleapis.com:443
    balancer_name: pick_first
    auth:
      authenticator: googleclientauth

service:
  extensions: [health_check, googleclientauth]
  telemetry:
    logs:
      level: warn
  pipelines:
    traces:
      receivers: [otlp]
      processors: [resourcedetection, resource/gcp_project_id, memory_limiter, batch]
      exporters: [otlp_grpc]
    metrics:
      receivers: [otlp]
      processors: [resourcedetection, resource/gcp_project_id, memory_limiter, batch]
      exporters: [otlp_grpc]
''')

# runtime_migrations.py
migrations = migrations_path.read_text()
lock_line = 'MIGRATION_ADVISORY_LOCK_ID = 0x48414F52  # "HAOR"\n'
if migrations.count(lock_line) != 1 or "RUNTIME_APPLICATION_ROLE" in migrations:
    raise SystemExit("migration role constant anchor mismatch")
migrations = migrations.replace(lock_line, lock_line + 'RUNTIME_APPLICATION_ROLE = "hao_runtime_app"\n', 1)
run_marker = 'def run_postgres_migrations(\n'
role_fn = '''def _ensure_runtime_application_role(conn: Any) -> None:
    """Keep long-lived Runtime credentials on DML-only privileges, not migration authority."""
    conn.execute(
        f"""
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{RUNTIME_APPLICATION_ROLE}') THEN
            CREATE ROLE {RUNTIME_APPLICATION_ROLE} NOLOGIN;
          END IF;
        END
        $$
        """
    )
    conn.execute(f"GRANT CONNECT ON DATABASE runtime TO {RUNTIME_APPLICATION_ROLE}")
    conn.execute(f"GRANT USAGE ON SCHEMA public TO {RUNTIME_APPLICATION_ROLE}")
    conn.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {RUNTIME_APPLICATION_ROLE}"
    )
    conn.execute(
        f"GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {RUNTIME_APPLICATION_ROLE}"
    )
    conn.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {RUNTIME_APPLICATION_ROLE}"
    )
    conn.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {RUNTIME_APPLICATION_ROLE}"
    )


'''
if migrations.count(run_marker) != 1:
    raise SystemExit("run_postgres_migrations anchor mismatch")
migrations = migrations.replace(run_marker, role_fn + run_marker, 1)
commit_line = '        conn.execute("COMMIT")\n'
if migrations.count(commit_line) != 1:
    raise SystemExit("migration commit anchor mismatch")
migrations = migrations.replace(commit_line, '        _ensure_runtime_application_role(conn)\n' + commit_line, 1)
main_call = '''    run_postgres_migrations(
        database_url,
        target_version=target_version,
        release_id=release_id,
    )
'''
if migrations.count(main_call) != 1:
    raise SystemExit("migration main call mismatch")
migrations = migrations.replace(
    main_call,
    '''    result = run_postgres_migrations(
        database_url,
        target_version=target_version,
        release_id=release_id,
    )
    verified = verify_postgres_schema(database_url, expected_version=result.to_version)
    print(
        f"HAO_RUNTIME_MIGRATION_VERIFIED from={result.from_version} to={result.to_version} "
        f"applied={','.join(str(value) for value in result.applied_versions)} schema={verified}"
    )
''',
    1,
)
migrations_path.write_text(migrations)

# migration tests
tests = migration_test_path.read_text()
tests = tests.replace(
    '    MIGRATION_ADVISORY_LOCK_ID,\n',
    '    MIGRATION_ADVISORY_LOCK_ID,\n    RUNTIME_APPLICATION_ROLE,\n',
    1,
)
anchor = '    assert conn.calls[-1][0] == "COMMIT"\n'
extra = '''    role_sql = [sql for sql, _ in conn.calls if RUNTIME_APPLICATION_ROLE in sql]
    assert any("CREATE ROLE" in sql for sql in role_sql)
    assert any("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES" in sql for sql in role_sql)
    assert any("ALTER DEFAULT PRIVILEGES" in sql for sql in role_sql)
    assert max(i for i, (sql, _) in enumerate(conn.calls) if RUNTIME_APPLICATION_ROLE in sql) < next(
        i for i, (sql, _) in enumerate(conn.calls) if sql == "COMMIT"
    )
'''
if tests.count(anchor) != 1:
    raise SystemExit("migration test anchor mismatch")
tests = tests.replace(anchor, anchor + extra, 1)
idempotent_anchor = '''    assert result.to_version == CURRENT_RUNTIME_SCHEMA_VERSION


def test_migration_upgrades_prior_schema_one_to_current():
'''
if tests.count(idempotent_anchor) != 1:
    raise SystemExit("idempotent migration test anchor mismatch")
tests = tests.replace(
    idempotent_anchor,
    '''    assert result.to_version == CURRENT_RUNTIME_SCHEMA_VERSION
    assert any(RUNTIME_APPLICATION_ROLE in sql for sql, _ in conn.calls)


def test_migration_upgrades_prior_schema_one_to_current():
''',
    1,
)
migration_test_path.write_text(tests)

# Terraform tests
staged = staged_test_path.read_text()
staged += '''

run "migration_enablement_requires_migration_inputs" {
  command = plan
  variables { enable_runtime_migration = true }
  expect_failures = [var.enable_runtime_migration]
}

run "migration_job_is_in_vpc_and_single_attempt" {
  command = plan
  variables {
    enable_runtime_migration               = true
    runtime_image                          = "example.invalid/runtime@sha256:0000000000000000000000000000000000000000000000000000000000000000"
    release_id                             = "migration-test-release"
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
'''
staged_test_path.write_text(staged)

rollout = rollout_test_path.read_text()
old = '  otel_exporter_otlp_endpoint    = "https://otel.example.invalid/v1/traces"\n'
new = '  otel_collector_config_secret_version = "1"\n'
if rollout.count(old) != 1:
    raise SystemExit("rollout OTel input anchor mismatch")
rollout_test_path.write_text(rollout.replace(old, new, 1))

cloud_sql_test_path.write_text('''mock_provider "google" {}
mock_provider "google-beta" {}

variables {
  project_id                  = "hao-runtime-v2-test"
  region                      = "asia-east1"
  cloud_sql_edition           = "ENTERPRISE"
  cloud_sql_tier              = "db-custom-1-3840"
  cloud_sql_availability_type = "ZONAL"
}

run "enterprise_custom_tier_allowed" {
  command = plan
}

run "enterprise_plus_rejects_enterprise_custom_tier" {
  command = plan
  variables { cloud_sql_edition = "ENTERPRISE_PLUS" }
  expect_failures = [google_sql_database_instance.runtime]
}
''')

# README
readme = readme_path.read_text()
readme = readme.replace(
    '3. immutable Runtime and OTel Collector images by digest;\n',
    '3. an immutable Runtime image digest, an immutable Google-Built OTel Collector digest, and a numeric Secret Manager version containing `deploy/otel-collector.yaml`;\n',
    1,
)
old_otel = "`otel_collector_image` must be an immutable digest for an image containing the repository's `deploy/otel-collector.yaml`. This candidate does not publish that image.\n\nThe collector receives the non-secret upstream endpoint as `OTEL_EXPORTER_OTLP_ENDPOINT`; backend authentication remains an external deployment decision."
new_otel = "`otel_collector_image` is the Google-Built OpenTelemetry Collector pinned by immutable digest. The repository config is supplied through a numeric Secret Manager version mounted at `/etc/otelcol-google/config.yaml`; a custom collector image build is not required unless a future component/platform gap is proven. The current config exports traces and metrics directly to `telemetry.googleapis.com:443` with `googleclientauth`, so no external OTLP backend endpoint is a Runtime deployment prerequisite."
if readme.count(old_otel) != 1:
    raise SystemExit("README OTel section mismatch")
readme = readme.replace(old_otel, new_otel, 1)
readme += '''

## Staged Runtime migration boundary

Base infrastructure remains plan/apply-capable with Runtime workloads and migration disabled. After an immutable Runtime image and a migration-only database credential exist, `enable_runtime_migration=true` creates one dedicated Cloud Run v2 Job using Direct VPC egress and the dedicated migration service account. The Job runs `python -m src.runtime_migrations`, has `max_retries=0`, and must complete successfully before API/Worker deployment is admitted. Production Runtime startup continues to verify rather than initialize schema.

The migration credential is separate from the long-lived `HAO_DATABASE_URL`. Migrations ensure a fixed `hao_runtime_app` NOLOGIN role has Runtime DML/default privileges; the long-lived built-in Runtime database user is expected to bind that role and must not retain `cloudsqlsuperuser` merely for convenience.

The base-stage outputs include the Cloud SQL private IP, migration service identity, and optional migration Job name so later stages consume exact provider state rather than reconstructing it.
'''
readme_path.write_text(readme)

# manual image publication workflow
image_workflow_path.write_text(r'''name: Runtime v2 Image Publish

on:
  workflow_dispatch:

permissions:
  contents: read
  id-token: write

concurrency:
  group: runtime-v2-image-publish
  cancel-in-progress: false

jobs:
  publish-runtime-image:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    env:
      GCP_PROJECT_ID: ${{ vars.GCP_PROJECT_ID }}
      GCP_WIF_PROVIDER: ${{ vars.GCP_WIF_PROVIDER }}
      GCP_DEPLOY_SERVICE_ACCOUNT: ${{ vars.GCP_DEPLOY_SERVICE_ACCOUNT }}
      GCP_REGION: asia-east1
      ARTIFACT_REPOSITORY: hao-runtime-v2-containers
      IMAGE_NAME: hao-runtime-v2
    steps:
      - name: Fail closed on shared EXP ref and deployment identifiers
        shell: bash
        run: |
          set -euo pipefail
          [[ "${GITHUB_REF}" == "refs/heads/exp/execution-control-plane-v1" ]] || { echo "Image publication is restricted to the shared EXP ref" >&2; exit 1; }
          for name in GCP_PROJECT_ID GCP_WIF_PROVIDER GCP_DEPLOY_SERVICE_ACCOUNT; do
            [[ -n "${!name:-}" ]] || { echo "Missing required variable: ${name}" >&2; exit 1; }
          done

      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7
        with:
          persist-credentials: false

      - name: Authenticate with GitHub OIDC
        id: google-auth
        uses: google-github-actions/auth@7c6bc770dae815cd3e89ee6cdf493a5fab2cc093 # v3
        with:
          project_id: ${{ env.GCP_PROJECT_ID }}
          workload_identity_provider: ${{ env.GCP_WIF_PROVIDER }}
          service_account: ${{ env.GCP_DEPLOY_SERVICE_ACCOUNT }}
          token_format: access_token

      - name: Build, push, and read back immutable Runtime digest
        shell: bash
        env:
          ACCESS_TOKEN: ${{ steps.google-auth.outputs.access_token }}
        run: |
          set -euo pipefail
          REGISTRY="${GCP_REGION}-docker.pkg.dev"
          IMAGE_BASE="${REGISTRY}/${GCP_PROJECT_ID}/${ARTIFACT_REPOSITORY}/${IMAGE_NAME}"
          IMAGE_TAG="${IMAGE_BASE}:${GITHUB_SHA}"
          printf '%s' "${ACCESS_TOKEN}" | docker login -u oauth2accesstoken --password-stdin "https://${REGISTRY}"
          docker build -f ai-space-generator/Dockerfile.runtime -t "${IMAGE_TAG}" ai-space-generator
          docker push "${IMAGE_TAG}"
          DIGEST="$(docker buildx imagetools inspect "${IMAGE_TAG}" | awk '/^Digest:/ {print $2; exit}')"
          [[ "${DIGEST}" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "Registry digest readback failed" >&2; exit 1; }
          IMMUTABLE_REF="${IMAGE_BASE}@${DIGEST}"
          python - <<'PY'
          import json, os
          payload = {
              "project_id": os.environ["GCP_PROJECT_ID"],
              "region": os.environ["GCP_REGION"],
              "repository": os.environ["ARTIFACT_REPOSITORY"],
              "image_name": os.environ["IMAGE_NAME"],
              "git_sha": os.environ["GITHUB_SHA"],
              "tag": os.environ["IMAGE_TAG"],
              "digest": os.environ["DIGEST"],
              "immutable_ref": os.environ["IMMUTABLE_REF"],
          }
          open("runtime-image-digest.json", "w").write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
          PY
          cat runtime-image-digest.json >> "${GITHUB_STEP_SUMMARY}"
        env:
          IMAGE_TAG: ${{ env.IMAGE_TAG }}
          DIGEST: ${{ env.DIGEST }}
          IMMUTABLE_REF: ${{ env.IMMUTABLE_REF }}

      - name: Preserve exact image digest handoff
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4
        with:
          name: runtime-v2-image-digest-${{ github.sha }}
          path: runtime-image-digest.json
          if-no-files-found: error
''')

# Correct the publish workflow to use a single shell step for JSON because shell locals are not job env.
image_text = image_workflow_path.read_text()
image_text = image_text.replace(
    '''          IMMUTABLE_REF="${IMAGE_BASE}@${DIGEST}"
          python - <<'PY'
          import json, os
          payload = {
              "project_id": os.environ["GCP_PROJECT_ID"],
              "region": os.environ["GCP_REGION"],
              "repository": os.environ["ARTIFACT_REPOSITORY"],
              "image_name": os.environ["IMAGE_NAME"],
              "git_sha": os.environ["GITHUB_SHA"],
              "tag": os.environ["IMAGE_TAG"],
              "digest": os.environ["DIGEST"],
              "immutable_ref": os.environ["IMMUTABLE_REF"],
          }
          open("runtime-image-digest.json", "w").write(json.dumps(payload, indent=2, sort_keys=True) + "\\n")
          PY
          cat runtime-image-digest.json >> "${GITHUB_STEP_SUMMARY}"
        env:
          IMAGE_TAG: ${{ env.IMAGE_TAG }}
          DIGEST: ${{ env.DIGEST }}
          IMMUTABLE_REF: ${{ env.IMMUTABLE_REF }}
''',
    '''          IMMUTABLE_REF="${IMAGE_BASE}@${DIGEST}"
          export IMAGE_TAG DIGEST IMMUTABLE_REF
          python - <<'PY'
          import json, os
          payload = {
              "project_id": os.environ["GCP_PROJECT_ID"],
              "region": os.environ["GCP_REGION"],
              "repository": os.environ["ARTIFACT_REPOSITORY"],
              "image_name": os.environ["IMAGE_NAME"],
              "git_sha": os.environ["GITHUB_SHA"],
              "tag": os.environ["IMAGE_TAG"],
              "digest": os.environ["DIGEST"],
              "immutable_ref": os.environ["IMMUTABLE_REF"],
          }
          open("runtime-image-digest.json", "w").write(json.dumps(payload, indent=2, sort_keys=True) + "\\n")
          PY
          cat runtime-image-digest.json >> "${GITHUB_STEP_SUMMARY}"
''',
)
image_workflow_path.write_text(image_text)

# manual migration execution workflow
migration_workflow_path.write_text(r'''name: Runtime v2 Migration Execute

on:
  workflow_dispatch:

permissions:
  contents: read
  id-token: write

concurrency:
  group: runtime-v2-migration-execute
  cancel-in-progress: false

jobs:
  execute-migration:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    env:
      GCP_PROJECT_ID: ${{ vars.GCP_PROJECT_ID }}
      GCP_WIF_PROVIDER: ${{ vars.GCP_WIF_PROVIDER }}
      GCP_DEPLOY_SERVICE_ACCOUNT: ${{ vars.GCP_DEPLOY_SERVICE_ACCOUNT }}
      GCP_REGION: asia-east1
      MIGRATION_JOB: hao-runtime-v2-migrate
    steps:
      - name: Fail closed on shared EXP ref and deployment identifiers
        shell: bash
        run: |
          set -euo pipefail
          [[ "${GITHUB_REF}" == "refs/heads/exp/execution-control-plane-v1" ]] || { echo "Migration execution is restricted to the shared EXP ref" >&2; exit 1; }
          for name in GCP_PROJECT_ID GCP_WIF_PROVIDER GCP_DEPLOY_SERVICE_ACCOUNT; do
            [[ -n "${!name:-}" ]] || { echo "Missing required variable: ${name}" >&2; exit 1; }
          done

      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7
        with:
          persist-credentials: false

      - name: Authenticate with GitHub OIDC
        uses: google-github-actions/auth@7c6bc770dae815cd3e89ee6cdf493a5fab2cc093 # v3
        with:
          project_id: ${{ env.GCP_PROJECT_ID }}
          workload_identity_provider: ${{ env.GCP_WIF_PROVIDER }}
          service_account: ${{ env.GCP_DEPLOY_SERVICE_ACCOUNT }}

      - name: Set up gcloud
        uses: google-github-actions/setup-gcloud@aa5489c8933f4cc7a4f7d45035b3b1440c9c10db # v3

      - name: Execute the in-VPC migration Job and wait for success
        shell: bash
        run: |
          set -euo pipefail
          gcloud run jobs describe "${MIGRATION_JOB}" --project "${GCP_PROJECT_ID}" --region "${GCP_REGION}" --format='value(name)' >/dev/null
          gcloud run jobs execute "${MIGRATION_JOB}" --project "${GCP_PROJECT_ID}" --region "${GCP_REGION}" --wait
''')
