# Runtime v2 Terraform infrastructure candidate

Status: **EXP / deployment-execution candidate / external bootstrap identifiers unresolved.**

Hao explicitly authorized completion of the deployment-execution-profile task on 2026-09-05. That authorization permits the bounded first-stage GCP bootstrap once the exact existing target project and required deployment identifiers are known. It does not permit inventing those identifiers or claiming resources exist without direct readback.

## Decision

Runtime v2 uses **Terraform HCL as the declarative infrastructure definition format**.

The selected deployment execution profile is now:

- GitHub Actions as the Terraform execution surface;
- GitHub OIDC -> Google Workload Identity Federation (WIF), with no long-lived Google service-account key;
- one dedicated Terraform deployment service account;
- Google Cloud Storage as the Terraform remote-state backend;
- Infrastructure Manager remains unselected as an additional executor/state Authority.

`backend.tf` is intentionally a **partial** `gcs` backend declaration. The real bucket and state prefix must be supplied explicitly at deployment time; no project ID, bucket name, WIF provider or deployment service-account identifier is fabricated or committed.

The one-time bootstrap that creates the protected state bucket, WIF provider and deployment identity lives in `bootstrap/`. See `bootstrap/README.md` for the exact bootstrap/state-migration sequence and external stop conditions.

The candidate pins:

- Terraform CLI `>= 1.14.0, < 2.0.0`
- `hashicorp/google = 8.0.0`
- `hashicorp/google-beta = 8.0.0`

`google-beta` is used only for the Cloud Run v2 Worker Pool surface while the rest of the Runtime stack uses the stable provider.

## What this candidate defines

- required Google Cloud service APIs
- dedicated VPC/subnet
- private service networking for Cloud SQL
- Docker Artifact Registry repository
- separate API and Worker service accounts
- Secret Manager **secret resources and IAM only**
- Cloud SQL for PostgreSQL with private IP, automated backups, PITR, and deletion protection
- Runtime database
- Cloud Run v2 API Service with `/startupz`, `/readyz`, `/livez` probes
- Cloud Run v2 Worker Pool with manual non-zero capacity and latest-revision instance split
- separate API/Worker Secret Manager access matching the role-scoped Runtime contract
- optional public Cloud Run invoker binding, disabled by default
- Runtime/OTel sidecar environment wiring

## Deployment bootstrap contract

The `bootstrap/` configuration defines only the execution/state bootstrap:

- one caller-supplied globally unique GCS state bucket;
- Object Versioning, Public Access Prevention, Uniform Bucket-Level Access, `force_destroy=false`, and Terraform `prevent_destroy`;
- one Terraform deployment service account;
- bounded project roles matching the currently managed resource families;
- bucket-scoped `roles/storage.objectAdmin` for Terraform state objects;
- one GitHub Workload Identity Pool/provider;
- WIF trust restricted to immutable GitHub repository ID `1304812158`, owner ID `133175353`, and `refs/heads/exp/execution-control-plane-v1`;
- `roles/iam.workloadIdentityUser` only for that trusted GitHub identity.

Primitive project roles `roles/owner` and `roles/editor` are not granted.

The first bootstrap necessarily requires an already authenticated Google principal because WIF does not exist yet. No service-account key should be created. After the first apply, bootstrap state must immediately be migrated from temporary local state into the protected GCS bucket under an isolated bootstrap prefix.

The manual GitHub workflow `.github/workflows/runtime-v2-gcp-wif-preflight.yml` is deliberately non-applying. After bootstrap identifiers have been installed as GitHub Actions variables, it verifies the exact shared EXP ref, obtains short-lived Google credentials through WIF, initializes the protected GCS backend, and validates the Runtime Terraform configuration. It does not run `terraform apply`.

## Deliberately not stored in Terraform

No real secret value is defined with `google_secret_manager_secret_version`.

This is intentional: Terraform state must not become a second secret store. Numeric secret versions are input references only.

Before Runtime workloads may be enabled, an explicitly authorized bootstrap must provide:

1. the numeric Secret Manager versions referenced by variables;
2. a least-privilege PostgreSQL user/password and a `HAO_DATABASE_URL` secret version matching that user;
3. immutable Runtime and OTel Collector images by digest;
4. Temporal Cloud and OAuth/IdP values;
5. any DNS/TLS/public-invoker/Workspace permissions explicitly authorized by Hao.

`enable_runtime_workloads` defaults to `false`, allowing the infrastructure definition to represent base resources without pretending secret/database-user bootstrap already exists.

## Cloud SQL edition and tier contract

`cloud_sql_edition` is an explicit required input. This is deliberate: with PostgreSQL 16+ an omitted edition can resolve to Enterprise Plus, while Enterprise shared-core and `db-custom-*` tiers are not valid Enterprise Plus tiers.

The configuration therefore:

- permits only `ENTERPRISE` or `ENTERPRISE_PLUS` as the edition value;
- writes the chosen edition explicitly into the Cloud SQL instance settings;
- blocks `ENTERPRISE_PLUS` combined with `db-custom-*`, `db-f1-micro`, or `db-g1-small` before resource operations;
- leaves the exact machine tier and `ZONAL` versus `REGIONAL` availability explicit because those are cost/reliability deployment decisions.

The repository's Terraform tests use mocked Google providers, so the valid/invalid edition-tier contract can be exercised with `terraform test` without GCP credentials or infrastructure creation.

## Database authentication choice

The first candidate models the database connection as a **Secret Manager-backed password-bearing PostgreSQL URL**, because that path is already enforced by the current Runtime.

Direct VPC provides network connectivity to the private Cloud SQL address, but it does not by itself provide PostgreSQL authentication.

Cloud SQL IAM DB authentication / connector / proxy is **not** silently assumed. Adopting one later is a separate engineering/security decision.

## OTel sidecar

`otel_collector_image` must be an immutable digest for an image containing the repository's `deploy/otel-collector.yaml`. This candidate does not publish that image.

The collector receives the non-secret upstream endpoint as `OTEL_EXPORTER_OTLP_ENDPOINT`; backend authentication remains an external deployment decision.

## Static, mocked, and deployment-preflight verification

Allowed before exact external identifiers exist:

```bash
terraform fmt -check -recursive
terraform init -backend=false
terraform validate
terraform test
```

`terraform test` uses mock providers for this configuration and must not require real Google Cloud credentials.

After the one-time state/WIF bootstrap exists, the manual WIF preflight may authenticate and initialize the GCS backend. A real-project `terraform plan` still requires every explicit deployment input; missing production values must not be replaced with fabricated placeholders merely to make the plan succeed.

No real `terraform apply`, `terraform destroy`, or mutating `gcloud` command is evidence-free or implicit.

No `*.tfvars` containing real project IDs, credentials, provider targets, or secret values should be committed.

## Current external blocker

The exact Runtime v2 Google Cloud project ID/project number, globally unique state bucket name, bootstrap principal and resulting WIF/service-account resource identifiers are not present in the repository or current Hao System deployment records.

Hao's authorization removes the previous **permission** gate for the bounded first-stage bootstrap, but it does not remove this **target identity/evidence** gate. Real GCP mutation therefore remains blocked until those identifiers can be resolved from an authoritative source or supplied directly.

## Deployment boundary

Engineering validation of this directory does not establish:

- Deployment PASS
- Cloud SQL restore/RPO/RTO field evidence
- Secret rotation field evidence
- Temporal production rollout
- OAuth/IdP production identity
- provider production cutover
- Natural-use PASS
- System PASS
