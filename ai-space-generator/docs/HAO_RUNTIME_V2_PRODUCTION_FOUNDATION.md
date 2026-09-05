# Hao System Runtime v2 — Production Foundation Engineering Package

Status: engineering package only. This document does **not** authorize provisioning, billing, IAM changes, secret creation, production deployment, provider mutation, or cutover.

## 1. One deployment identity

The API service and Temporal worker pool MUST use the same immutable application image digest and a compatible configuration set: Runtime environment, region, Postgres database, Temporal namespace/task queue, MCP public resource identity, OAuth issuer/audience/resource, request-state key ring/audience, expected Hao subject, action targets/policies/parent plans, and attestation key ID.

A release is deployable only when the exact source SHA, image digest, migration version, and test evidence are recorded together by the Integration Authority. Environment-variable parity is necessary but is not itself proof of production identity.

## 2. Cloud Run Runtime API

The container uses exec-form startup, a non-root UID, port 8080, and `STOPSIGNAL SIGTERM`.

Runtime endpoints:

- `/livez`: process liveness only; it must not depend on downstream services.
- `/readyz`: runtime readiness; it reads authoritative operational state and returns 503 if the persistence boundary is unavailable.
- `/healthz`: compatibility alias for readiness during migration of infrastructure configuration.
- `/mcp`: Streamable HTTP MCP resource-server endpoint. The host application's lifespan MUST enter `mcp.session_manager.run()` because mounted Starlette sub-applications do not automatically receive their own lifespan.

Production infrastructure SHOULD configure startup/liveness/readiness probes separately. Cloud Run readiness probes are a platform capability gate and must be verified against the actual selected service configuration before cutover.

Graceful shutdown contract: Cloud Run sends SIGTERM before forced termination. Uvicorn must receive SIGTERM directly; exporter shutdown runs from application lifespan after MCP request handling stops.

## 3. Cloud Run Worker Pool

The worker uses the same image as the API and selects worker behavior by `HAO_RUNTIME_ROLE=worker`.

A production worker pool MUST start and remain at a non-zero instance count while Runtime work is admitted. Worker pools do not provide service-style autoscaling semantics; capacity is an explicit deployment decision.

Temporal `Worker.run()` owns SIGINT/SIGTERM handling. The Runtime configures an 8-second `graceful_shutdown_timeout`, leaving margin inside Cloud Run's approximately 10-second termination window. OTel exporters are shut down after worker termination.

Canary rollout uses a new worker-pool revision with bounded instance allocation. Do not remove the previous compatible worker revision until replay/compatibility checks and bounded field evidence pass.

## 4. PostgreSQL migration contract

`src/runtime_migrations.py` is the production startup migration gate.

Properties:

- `hao_runtime_schema_migrations` records applied versions.
- `pg_advisory_xact_lock` serializes concurrent API/worker startup migration attempts.
- migration transaction isolation is SERIALIZABLE.
- version 1 reproduces the existing Runtime v2 tables and uniqueness constraints; it does not change provider/reconciliation/parent semantics.
- a binary that sees a migration newer than it knows fails closed with `DATABASE_SCHEMA_NEWER_THAN_RUNTIME`.
- migrations are upgrade-only. Rollback of application code must therefore target a binary that is explicitly compatible with the current schema; destructive down-migrations are not an automatic rollback mechanism.

Before first production traffic, run the same migration code against the production Cloud SQL target as an explicit pre-traffic step and verify the migration ledger. Concurrent startup execution remains safe as a defense-in-depth path.

### Backup / PITR / restore drill

Cloud SQL automated backups and PITR MUST be enabled and verified on the real instance. A restore drill is not satisfied by documentation: restore to a separate instance, point a non-production Runtime revision at the restored database, run read-only integrity/readiness checks, and record RPO/RTO evidence. Only then may the Integration Authority classify restore readiness as passed.

## 5. Secret Manager boundary

The production container requires secret material through runtime injection, not source control or baked image layers.

Secret-bearing values:

- `HAO_DATABASE_URL`
- `HAO_TEMPORAL_API_KEY`
- `HAO_ATTESTATION_SECRET`
- `HAO_MCP_REQUEST_STATE_KEYS`

Sensitive deployment-owned configuration that must be protected from unauthorized mutation even when not secret:

- `HAO_EXPECTED_SUBJECT`
- `HAO_ATTESTATION_KEY_ID`
- OAuth issuer/resource/audience/JWKS identity
- action targets, policies, parent plans

Rotation:

- request-state keys are an ordered key ring: deploy new+old, verify multi-instance retries, then remove old.
- attestation rotation requires a new key ID and an explicit compatibility/verification plan; never silently reuse an ID for different key material.
- Temporal/API/database credential rotation must be staged so old and new revisions can coexist during bounded rollout when the provider supports overlap.
- revoked/missing required production secrets must make startup fail closed through `RuntimeSettings` or downstream connection initialization.

No real secret values belong in this repository.

## 6. OpenTelemetry assembly

Production uses OTLP/HTTP exporters and SHOULD send them to an OpenTelemetry Collector or an equivalent controlled OTLP endpoint rather than embedding backend-specific telemetry logic into Runtime semantics.

Telemetry constraints:

- run IDs and failure codes are trace-only high-cardinality attributes.
- metrics use bounded event/phase/provider/failure-stage dimensions.
- action arguments, Google payloads/readbacks, OAuth tokens, credentials, signing material, model prompts/responses, and private content are forbidden telemetry payloads.
- trace and metric providers are shut down during API/worker graceful termination to flush bounded telemetry.

Production acceptance requires collector/exporter reachability, retention/access policy, sampling and backend-cardinality verification on the real observability stack.

## 7. Release, canary and rollback

For every candidate, Integration Authority records:

1. source SHA;
2. immutable image digest;
3. schema migration version;
4. API revision and worker revision identifiers;
5. Temporal namespace/task queue;
6. targeted and full regression evidence;
7. live/readiness and MCP transport smoke evidence;
8. rollback target.

API: create a new Cloud Run revision, verify startup/liveness/readiness and authenticated MCP smoke tests before increasing traffic. Roll back traffic to the prior compatible revision on health, authorization, persistence, or telemetry regression.

Worker: replay representative persisted Temporal histories against the candidate workflow code before rollout. Runtime workflow code must remain deterministic. A worker rollout must preserve compatibility with in-flight histories; if later adopting Temporal Worker Deployment Versioning, treat that as a separate engineering change and validation gate rather than assuming it exists here.

Database rollback: application rollback is allowed only when the prior application version understands the current schema. Otherwise restore/forward-fix is required; never improvise a destructive down-migration during an incident.

## 8. Gates that remain external

This package can support **Engineering PASS** after its exact-SHA CI succeeds. It cannot establish any of the following without real authorized resources and readback evidence:

- GCP project/billing readiness;
- IAM/service-account correctness;
- Artifact Registry/image provenance;
- Cloud Run service or worker-pool deployment;
- Cloud SQL availability, backups, PITR, restore drill, capacity or connection limits;
- Secret Manager creation, access policy, rotation or revocation;
- Temporal Cloud namespace/API-key connectivity and replay of real histories;
- OAuth IdP/JWKS production configuration;
- OTel collector/backend connectivity, retention and access controls;
- canary/rollback execution under production traffic.

Do not relabel architecture or engineering readiness as Deployment PASS or Production PASS.
