# Runtime v2 Terraform infrastructure candidate

Status: **EXP / non-provisioning candidate / `terraform apply` is not authorized.**

## Decision

Runtime v2 uses **Terraform HCL as the declarative infrastructure definition format**.

This directory intentionally does **not** configure a Terraform backend and does **not** select Infrastructure Manager, HCP Terraform, Cloud Storage state, or any other remote-state/executor as authority. State ownership is a separate operational/cost/IAM decision.

The candidate pins:

- Terraform CLI `>= 1.14.0, < 2.0.0`
- `hashicorp/google = 8.0.0`
- `hashicorp/google-beta = 8.0.0`

`google-beta` is used only for the Cloud Run v2 Worker Pool surface while the rest of the stack uses the stable provider.

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

## Static and mocked verification only

Allowed in the isolated candidate:

```bash
terraform fmt -check -recursive
terraform init -backend=false
terraform validate
terraform test
```

`terraform test` uses mock providers for this configuration and must not require real Google Cloud credentials.

Not allowed without explicit Hao authorization:

```bash
terraform plan   # against a real project/credentials
terraform apply
terraform destroy
gcloud ...       # any real mutation
```

No `*.tfvars` containing real project IDs, credentials, provider targets, or secret values should be committed.

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
