# Runtime v2 deployment bootstrap

Status: **EXP / authorized deployment bootstrap package / real GCP execution requires exact target identifiers and an authenticated bootstrap principal.**

This directory owns only the one-time bootstrap required before the main Runtime v2 Terraform configuration can use GitHub Actions + Google Workload Identity Federation (WIF) + a GCS remote backend.

It does **not** create the Runtime API, Worker Pool, Cloud SQL instance, Runtime secret values, Temporal namespace, OAuth/IdP configuration, DNS, provider cutover, or production traffic.

## Selected execution profile

Runtime v2 uses:

1. GitHub Actions as the Terraform execution surface;
2. GitHub OIDC -> Google Workload Identity Federation;
3. one dedicated Google service account for Terraform deployment;
4. Google Cloud Storage as the Terraform remote-state backend;
5. a separate GCS prefix for bootstrap state and Runtime production state;
6. no long-lived Google service-account key in GitHub.

Infrastructure Manager remains unselected. Adopting it later would create a second managed deployment/revision/state authority and requires a separate decision.

## Trust boundary

The WIF provider is intentionally restricted to the immutable GitHub identity already verified for this repository:

- repository ID: `1304812158` (`1918293/-ai-space-generator`)
- repository owner ID: `133175353`
- trusted ref: `refs/heads/exp/execution-control-plane-v1`

The deployment identity is **not** granted primitive `roles/owner` or `roles/editor`.

Project roles granted to the deployment service account are bounded to the current Terraform resource surface:

- `roles/artifactregistry.admin`
- `roles/cloudsql.admin`
- `roles/compute.networkAdmin`
- `roles/iam.serviceAccountAdmin`
- `roles/iam.serviceAccountUser`
- `roles/run.admin`
- `roles/secretmanager.admin`
- `roles/servicenetworking.networksAdmin`
- `roles/serviceusage.serviceUsageAdmin`

The Terraform state bucket grants the deployment service account only `roles/storage.objectAdmin` on that bucket.

## State bucket contract

The bootstrap creates exactly one caller-supplied, globally unique bucket name. The bucket is configured with:

- `force_destroy = false`
- Public Access Prevention = `enforced`
- Uniform Bucket-Level Access = `true`
- Object Versioning = enabled
- Terraform `prevent_destroy = true`

The bucket name is never invented or committed to source control.

## First bootstrap sequence

The first bootstrap cannot use WIF because WIF does not exist yet. It therefore requires an already authenticated Google principal with explicit permission to:

- enable the four bootstrap APIs;
- create the state bucket;
- create a service account;
- create a Workload Identity Pool/provider;
- grant the bounded project roles above;
- grant `roles/iam.workloadIdentityUser` on the deployment service account.

No service-account key should be created for this purpose.

From this directory:

```bash
terraform init -backend=false -input=false
terraform plan \
  -input=false \
  -var="project_id=<EXACT_EXISTING_PROJECT_ID>" \
  -var="state_bucket_name=<EXACT_GLOBALLY_UNIQUE_BUCKET_NAME>" \
  -out=bootstrap.tfplan
terraform apply bootstrap.tfplan
```

Before apply, the plan must be inspected for an exact project match and an exact resource set. Stop if the project ID is not the explicitly selected Runtime v2 target project or if the state bucket name unexpectedly already exists under another owner.

After apply, capture these outputs:

```bash
terraform output -raw state_bucket_name
terraform output -raw state_prefix
terraform output -raw workload_identity_provider
terraform output -raw deployment_service_account
terraform output -raw trusted_github_ref
```

Then immediately migrate the bootstrap state into the protected bucket so no unmanaged local bootstrap state remains:

```bash
terraform init -migrate-state -input=false \
  -backend-config="bucket=<OUTPUT_STATE_BUCKET_NAME>" \
  -backend-config="prefix=hao-runtime-v2/bootstrap"
```

Read back the remote state after migration before deleting any local state backup.

## GitHub Actions identifiers after bootstrap

The following are identifiers, not secret values, and are expected as GitHub Actions repository/environment variables before the WIF preflight can run:

- `GCP_PROJECT_ID`
- `GCP_WIF_PROVIDER`
- `GCP_DEPLOY_SERVICE_ACCOUNT`
- `TF_STATE_BUCKET`
- `TF_STATE_PREFIX` (expected production prefix: `hao-runtime-v2/production`)

The WIF preflight workflow fails closed if any value is absent or if it is dispatched from a ref other than `exp/execution-control-plane-v1`.

## Credentialed Runtime plan boundary

Successful WIF authentication and GCS backend initialization are **not** Deployment PASS. They only establish that the execution identity and state authority are reachable.

A real-project Runtime `terraform plan` additionally requires all explicit deployment inputs from the parent module, including cost-sensitive Cloud SQL edition/tier/availability, immutable image digests, OAuth/Temporal endpoints and existing numeric Secret Manager versions. None of those values may be fabricated to make a real plan succeed.

`terraform apply`, secret-value bootstrap, database-user creation/migration, Cloud SQL restore drills, Temporal rollout, OAuth/IdP configuration, provider cutover, PR merge and production cutover remain separate evidence gates.
