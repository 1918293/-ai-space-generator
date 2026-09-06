# Hao System Runtime v2 — Production Foundation Engineering Runbook (EXP)

## Status and authority

This is a bounded Lane B engineering package subordinate to `HAO_RUNTIME_V2.md` and `HAO_RUNTIME_V2_DEPLOYMENT.md`. It does not provision, authorize, deploy, cut over, or promote Hao System Current. Passing these checks is **Engineering PASS only**; it is never Deployment PASS, Natural-use PASS, or Runtime v2 completion.

Lane B may prepare deployable code and deterministic gates. Real GCP resources, billing, IAM, credentials, Secret Manager payloads, Cloud SQL instances, Temporal Cloud namespace configuration, DNS/TLS, production rollout, and cutover remain external authorization/resource gates.

## 1. Immutable release identity

Every production API and worker process must load the same non-secret deployment identity:

- `HAO_RUNTIME_ENV=production`
- `HAO_RUNTIME_REGION`
- `HAO_RELEASE_ID`: immutable application release identifier, normally the exact source/image provenance identifier.
- `HAO_DEPLOYMENT_ID`: deployment/revision group identifier.
- `HAO_DATABASE_URL`
- `HAO_DATABASE_SCHEMA_VERSION`
- Temporal endpoint, namespace, task queue, and `HAO_TEMPORAL_WORKER_VERSION`
- public MCP resource URL and OAuth issuer/resource/audience
- expected Hao subject
- attestation key ID
- OTel endpoint

`RuntimeSettings.deployment_identity_fingerprint` hashes this identity while deliberately excluding `HAO_RUNTIME_ROLE` and secret values. API and worker readbacks must report the same fingerprint before a rollout is eligible to advance. A different `api` versus `worker` role is expected; a different deployment fingerprint is not.

## 2. Fail-closed production configuration

Production startup fails if any required deployment field is missing or malformed. Additional Lane B gates are:

- PostgreSQL is mandatory.
- `HAO_DATABASE_SCHEMA_VERSION` must be a positive supported version and must exactly match the binary-supported Runtime schema.
- `HAO_DATABASE_RPO_SECONDS` must be positive and no greater than 300 seconds.
- `HAO_DATABASE_RTO_SECONDS` must be positive. The declared value is a requirement, not evidence that it has been achieved.
- `HAO_WORKER_INSTANCE_COUNT >= 1`.
- `HAO_GRACEFUL_SHUTDOWN_SECONDS` must fit inside Cloud Run's termination window; the current engineering default is 8 seconds and production rejects values above 9 seconds.
- `HAO_TEMPORAL_WORKER_VERSION` is mandatory and immutable for a release.
- production OTel export uses HTTPS unless the application exports to a loopback collector (`127.0.0.1`, `localhost`, or `::1`).
- `HAO_SECRET_BINDINGS_JSON` is required metadata for secret-backed values and must bind each required secret to a **numeric immutable Secret Manager version**, never `latest` or another moving alias.

Unconditionally required secret bindings currently include:

- `HAO_TEMPORAL_API_KEY`
- `HAO_ATTESTATION_SECRET`
- `HAO_MCP_REQUEST_STATE_KEYS`

Conditional secret bindings are fail-closed when the corresponding secret-bearing configuration is used:

- `HAO_ATTESTATION_PREVIOUS_KEYS_JSON` is required in the binding map when the previous-key verification map is non-empty.
- `HAO_DATABASE_URL` is required in the binding map when the PostgreSQL URL embeds a password. A passwordless/workload-bound URL does not force one specific Cloud SQL authentication mechanism.

The actual secret values remain injected at runtime and are never stored in this repository. A missing injected value is already a startup failure through the normal Runtime settings contract.

A brand-new empty `operational_state` also requires deployment-owned `HAO_INITIAL_MODE` and `HAO_INITIAL_TASK`. These values seed first boot only; once durable operational state exists, that state remains authoritative.

## 3. Container startup and shutdown contract

`Dockerfile.runtime` runs as an unprivileged UID and declares `STOPSIGNAL SIGTERM`.

### API lifecycle

The API builds all required configuration and production dependencies before becoming startup-ready. It exposes:

- `/livez`: process liveness only; must not depend on Cloud SQL or external providers.
- `/startupz`: startup completion; the application is not considered started until its dependency assembly succeeded.
- `/readyz`: traffic readiness; verifies exact database schema compatibility and durable operational-state readability, and returns `503` while draining.
- `/healthz`: compatibility alias for readiness.

Cloud Run startup, liveness, and readiness probes are generally available. The production baseline still must not treat the readiness probe as the only admission boundary: an instance's startup-success condition must itself mean that the process is safe to serve, while `/readyz` remains the explicit runtime/readback check used for canary and synthetic validation. Keep the zero-traffic/tagged revision gate before traffic migration.

Recommended service probe intent:

- startup: HTTP `/startupz`, short interval, enough failure budget for dependency initialization;
- liveness: HTTP `/livez`; never include database reachability in liveness;
- readiness: HTTP `/readyz`.

### Worker lifecycle

Cloud Run Worker Pools are generally available. They do not provide a native request-driven autoscaler, although an external control plane can adjust instance count. Runtime v2 therefore keeps an explicitly managed, fixed non-zero worker count as the conservative initial production baseline; later external autoscaling is a separate deployment decision and must not change Runtime idempotency/reconciliation semantics.

The worker installs SIGTERM/SIGINT handlers, stops polling through Temporal worker shutdown, gives Temporal most of the configured termination budget, then performs a bounded OTel flush. Provider semantics are not changed by shutdown: unknown-effect work remains subject to existing idempotency/reconciliation rules.

## 4. PostgreSQL migrations, locking, and concurrency

Application processes no longer own production schema creation. Production API/worker construction uses existing stores with `initialize_schema=False` and first calls `verify_postgres_schema`.

The release migration entrypoint is:

```text
python -m src.runtime_migrations
```

Required environment:

- `HAO_DATABASE_URL`
- `HAO_RELEASE_ID`
- `HAO_DATABASE_SCHEMA_VERSION`

`runtime_migrations.py` provides:

1. a versioned `runtime_schema_migrations` ledger;
2. ordered migrations;
3. one PostgreSQL transaction at `SERIALIZABLE` isolation;
4. `pg_advisory_xact_lock` to serialize competing migration runners;
5. rollback on failure;
6. refusal when the database schema is newer than the application target;
7. exact-version/readiness verification before API or worker starts.

The current engineering schema is **v3**:

- v1 centralizes the original durable Runtime v2 tables and their uniqueness contracts, including operational events, idempotency keys, authoritative completions, MCP workflow ownership, reconciliation action IDs, parent-child action IDs, and stable finalization issuance reservations;
- v2 adds parent-task row revision/CAS support, child dependency persistence, and reconciliation retry reservations;
- v3 adds `authoritative_completions.key_id`, making completion signing-key identity durable and allowing restart/rotation verification to distinguish the exact key that minted a receipt.

Existing runtime store transactions remain serializable and use row locking/CAS or uniqueness constraints for concurrent state transitions. The migration lock protects schema transition; it does not replace application-level locking.

### Migration release sequence

1. Confirm the target Cloud SQL instance has verified backups/PITR configured; do not infer this from Cloud SQL defaults.
2. Record the exact release/image digest and target schema version.
3. Run exactly one authorized migration execution; simultaneous runners are serialized by the advisory transaction lock but unnecessary duplication should still be avoided.
4. Execute `verify_postgres_schema` from the exact candidate release.
5. Only then allow candidate API/worker revisions to start.
6. If migration fails, keep serving the old compatible revision; do not force-start a runtime against a mismatched database.

A destructive/non-backward-compatible future migration requires its own expand/migrate/contract plan. Cloud Run traffic rollback cannot undo a destructive database migration.

## 5. Backup, PITR, RPO, RTO, and restore drill

Runtime production requires PITR and automated backup evidence from the real Cloud SQL instance. Cloud SQL PITR typically offers an RPO of five minutes or less, which is why production configuration rejects a declared RPO above 300 seconds. This is a requirement boundary, not proof that a specific instance meets it.

The first proposed RTO requirement is 3600 seconds unless Integration Authority selects a stricter value. `HAO_DATABASE_RTO_SECONDS` records the chosen requirement; only a real restore drill can satisfy it.

### Restore-drill acceptance procedure

1. Choose a known recovery timestamp and record source instance identity plus latest recovery time.
2. PITR to a **new isolated instance**. Do not overwrite production during the drill.
3. Measure from restore initiation until database connectivity, exact schema verification, and read-only Runtime invariant checks are ready.
4. Verify authoritative completion uniqueness, idempotency state, operational state, workflow ownership, reconciliation rows, parent-task rows, and stable finalization reservations are readable and internally consistent.
5. Confirm the measured data-loss window is within configured RPO and measured recovery-ready time is within configured RTO.
6. Destroy or quarantine the isolated drill instance only under the appropriate real-resource authorization.

Until this has been executed against a real instance, report the restore contract as **testable but unverified**.

## 6. Secret Manager rotation and revocation

Secret Manager secret versions are immutable. Production rollout must bind numeric versions, not `latest`, so application rollback also restores the previous credential binding.

Completion signing rotation is key-ID aware in the Runtime engineering path: the current non-empty `HAO_ATTESTATION_KEY_ID` is cryptographically included in the signed completion payload and persisted in `authoritative_completions.key_id`. `HAO_ATTESTATION_PREVIOUS_KEYS_JSON` is verification-only; a retained prior key can verify/replay a receipt minted before rotation, but it cannot become the current signing key. Removing a prior key ID from that map makes receipts carrying that key ID unverifiable by the new runtime and is the explicit Runtime-level revocation behavior.

Rotation sequence:

1. create a new secret version outside source control;
2. assign a new signing key ID when rotating completion signing material;
3. keep any still-valid prior key in the verification-only previous-key map and bind that map to an immutable Secret Manager version;
4. update `HAO_SECRET_BINDINGS_JSON` metadata and runtime secret injection to the exact numeric versions;
5. deploy as a candidate revision with zero traffic / bounded worker allocation;
6. pass startup, OAuth/identity-owned gates from the relevant lanes, controlled canary, key-ID substitution/replay/revocation tests, and stable finalization checks;
7. advance the release;
8. retain the prior secret/version only during the rollback and in-flight-drain window in which old receipts must remain verifiable;
9. remove the prior key ID from the verification map and disable the old Secret Manager version after the compatibility window closes; destroy it only after direct evidence shows no compatible in-flight execution requires it.

Request-state sealing already accepts an ordered key tuple, which supports read-old/write-new style rotation when the owning protocol lane confirms the exact key ordering contract. This completion-attestation rotation contract does not redefine that protocol semantic.

## 7. OpenTelemetry production assembly

The application telemetry API is an allowlist. It records only bounded operational attributes; action/model/provider payloads, expected-state contents, OAuth tokens, signing material, and secret values are not accepted as telemetry fields. High-cardinality `run_id` is trace-only and is excluded from metric labels.

`deploy/otel-collector.yaml` is a candidate collector assembly with:

- loopback OTLP/HTTP receiver only;
- `memory_limiter` first;
- bounded `batch` processor;
- bounded exporter queue and retry window;
- no debug/logging exporter that would dump payloads;
- collector health extension;
- traces and metrics only.

Use a pinned OpenTelemetry Collector Contrib image version/digest at deployment time. Resource allocation must be verified against the configured `memory_limiter`; the file alone does not reserve Cloud Run memory. The upstream exporter endpoint and credentials remain deployment secrets/configuration and are not committed.

The application retains handles to trace and metric providers and performs bounded flush/shutdown during process termination.

## 8. API release rollout and rollback

Cloud Run service revisions are immutable and support zero-traffic deployment, revision tags, traffic splitting, and rollback.

Candidate sequence:

1. build image once and record immutable image digest + source SHA;
2. run unit/integration/replay tests for that exact SHA;
3. run the database migration gate if required;
4. deploy a new API revision with **0% production traffic** and a revision tag;
5. verify `/startupz`, `/livez`, `/readyz`, deployment fingerprint, schema version, OAuth/transport gates owned by Lane A/C, and non-sensitive OTel correlation;
6. send only bounded synthetic/canary traffic;
7. move traffic in explicit stages selected by Integration Authority (for example 5% -> 25% -> 50% -> 100%) only while acceptance signals remain healthy;
8. retain the prior compatible revision until the rollback window closes.

Rollback is traffic reassignment to the prior revision, not a rebuild. If the database schema has crossed a non-backward-compatible boundary, stop: traffic rollback is not safe until database compatibility is reconciled.

## 9. Worker Pool rollout, Temporal versioning, and rollback

Worker pools use **instance splitting**, not HTTP traffic splitting. Runtime v2's initial production baseline uses an explicitly managed non-zero allocation rather than assuming a request-driven autoscaler.

Every worker revision registers an immutable Temporal deployment version using the pinned SDK's `WorkerDeploymentConfig` and `WorkerDeploymentVersion`. Runtime v2 defaults controlled workflows to `PINNED` versioning behavior so a long-running workflow does not silently jump to a new build.

Candidate sequence:

1. replay representative history from the previous release against candidate workflow code;
2. deploy candidate worker revision with a bounded instance allocation while retaining old revision instances;
3. verify both revisions report the expected deployment identity and worker build IDs;
4. manage the Temporal deployment's current/routing state through an explicitly authorized deployment-plane operation;
5. observe workflow task reachability/open pinned histories before reducing old worker allocation;
6. drain the old worker through SIGTERM/Temporal graceful shutdown;
7. only scale old revision to zero when no workflow still requires it, or after an explicitly validated migration/continue-as-new strategy.

Rollback restores instance allocation to the previous worker revision and restores the Temporal deployment routing/current version as required. A Cloud Run rollback alone is not sufficient if Temporal deployment routing was advanced.

### Temporal replay gate

`tests/test_temporal_replay_compatibility.py` runs a real Temporal test environment, records a `HaoExecutionControlWorkflow` history, and replays it with `Replayer`. Any nondeterminism is a release blocker.

For future releases, CI must also retain representative **previous-release** histories as immutable replay fixtures/evidence. A same-release replay proves the mechanism and current determinism but cannot by itself prove an arbitrary future upgrade is compatible with every old history.

If a workflow change cannot replay old histories, use Temporal's supported workflow versioning/patching strategy or a separately validated drain/continue-as-new plan. Never force incompatible old histories onto a new worker and call the rollout safe.

## 10. Production deployment identity readback

Both `/readyz` and `/healthz` return only non-secret release metadata:

- role
- release ID
- deployment ID
- deployment identity fingerprint
- database schema version
- Temporal worker version

Do not expose database URLs, secret bindings, OAuth token values, request-state keys, attestation material, provider arguments, or private payloads in health responses.

## 11. Required real-resource gates that remain outside Lane B

This package deliberately does not perform or claim:

- GCP project/resource provisioning or billing enablement;
- IAM/service identity creation or mutation;
- Artifact Registry/image deployment;
- Cloud Run Service or Worker Pool deployment;
- Cloud SQL instance creation, backup/PITR configuration, real migration, or restore drill;
- Secret Manager secret creation/value writes/version disable/destroy;
- Temporal Cloud production namespace creation or deployment routing changes;
- real OAuth provider configuration;
- DNS/TLS setup;
- controlled provider production mutation/cutover;
- production traffic migration/cutover;
- Runtime v2 completion or Hao System Current promotion.

## 12. Integration checklist

Before Integration Authority consumes this production-foundation delta:

- compare the candidate branch against its exact shared starting SHA;
- preserve current schema v3 ordering and all earlier v1/v2 persistence semantics when resolving future overlaps;
- reconcile `runtime_deployment.py` with transport/OAuth/identity/provider/parent semantics rather than choosing one whole-file version;
- preserve `initialize_schema=False` + exact migration verification for production startup;
- preserve health/lifecycle/Temporal versioning assembly without weakening identity/provider/distributed semantics;
- run the full Runtime v2 test suite plus replay/migration/config/lifecycle/key-rotation regressions on the exact integrated resulting SHA;
- compile production entrypoints and build the runtime container on the exact integrated SHA;
- keep all real-resource and cutover gates explicitly NOT EXECUTED until direct deployment/readback evidence exists.
