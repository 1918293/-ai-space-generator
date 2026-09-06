# Hao System Execution Runtime v2 — Production Topology Decision (EXP)

## Status

This document defines the current Runtime v2 production architecture target and deployment contract. It is an EXP engineering decision, not evidence that any GCP resource, billing relationship, DNS name, OAuth tenant, Cloud SQL database, Temporal Cloud namespace, provider credential, or production workload has been provisioned.

Engineering PASS, Deployment PASS, Natural-use PASS, and System PASS remain separate evidence classes.

## 1. Selected topology

```text
ChatGPT / approved clients
        |
        | HTTPS + OAuth 2.1 bearer token
        v
Cloud Run Service — asia-east1
Hao Runtime API / Streamable HTTP MCP
        |
        +--------------------+
        |                    |
        v                    v
Cloud SQL PostgreSQL     Temporal Cloud Namespace
asia-east1               Namespace Endpoint + API key
                             |
                             | PINNED Worker Versioning
                             v
                   0..N active Worker Versions
                   one Cloud Run Worker Pool
                   per Temporal Worker Build ID
                             |
                             v
                         Tool Broker
                             |
                             v
                  Controlled Provider Adapters
```

Secrets: Google Secret Manager  
Observability: OpenTelemetry-compatible traces/metrics/events  
Semantic Authority: existing Hao System canonical stores  
Projection: Handoff / PR body / graphs / summaries / chat UI  
Authorization Server: standards-compliant external OAuth provider; Auth0 remains a candidate pending real client E2E evidence.

## 2. Region and infrastructure Authority

Current primary region: `asia-east1` (Taiwan).

Runtime API, Worker compute, and Cloud SQL should remain co-located in the primary region unless a later HA/DR decision explicitly changes that topology. Region is a deployment decision, not an application invariant.

Authority separation:

- Terraform owns GCP compute/infrastructure existence and configuration.
- Temporal owns durable workflow routing and Worker Version lifecycle.
- Runtime v2 owns execution/admission/completion semantics.
- Hao canonical stores remain semantic Authority.
- Provider receipt plus direct readback remains external-effect evidence.

Do not introduce a second overlapping deployment authority without an explicit authority migration.

## 3. Runtime API — Cloud Run Service

The API is the HTTP ingress and owns:

- `/mcp` Streamable HTTP endpoint;
- OAuth resource-server middleware;
- exact Host/Origin transport security;
- runtime context/status APIs;
- controlled-run submission/finalization;
- parent-task and reconciliation control surfaces;
- health/readiness endpoints;
- no consequential provider effect outside the Tool Broker boundary.

Requirements:

- stable HTTPS public hostname;
- explicit MCP Host allowlist and DNS-rebinding protection;
- production configuration validated at startup;
- role-scoped service identity and Secret Manager access;
- no secret values committed to GitHub.

API startup/readiness must include Runtime storage compatibility and operational-state checks. Platform health is not a substitute for Runtime admission/readback.

## 4. Durable Worker — Temporal PINNED rainbow on Cloud Run Worker Pools

Temporal workers are continuous pull workers. Cloud Run Worker Pool remains the selected compute baseline.

Runtime v2 uses Temporal Worker Versioning with immutable Worker Build IDs and `PINNED` default versioning behavior. PINNED workflows may outlive a deployment, including workflows waiting arbitrarily long for Hao authorization. Therefore production must support **rainbow coexistence** instead of assuming only one old/new worker pair.

Current engineering model:

- `0..N` Worker Versions may exist simultaneously;
- each Temporal Worker Build ID owns one deterministic Cloud Run Worker Pool;
- Terraform owns pool existence and manual instance count;
- Temporal owns Current/Ramping routing;
- a newly created candidate may exist with zero compute before activation;
- an old Worker Version must not be removed while pinned workflows can still route to it;
- after direct Temporal readback proves a version safely Drained, its pool may first be scaled to `0` and retained before later deletion;
- deletion must never be inferred only from Terraform input removal.

This deliberately avoids having two independent traffic percentages — Cloud Run revision splitting and Temporal workflow routing — for Worker rollout.

### Worker health layers

Worker health must be separated into two layers:

1. **Cloud Run startup/liveness** — local process/runtime health only. Liveness must detect unrecoverable local failure such as a dead process/event-loop failure; it must not fail merely because Temporal Cloud is temporarily unreachable.
2. **Temporal rollout admission** — direct server readback that the expected Worker Build ID has pollers for all expected task queues before ramp/current promotion.

Do not use missing-poller override flags as the normal rollout path.

Temporal Serverless Workers / Kubernetes Worker Controller remain outside the initial baseline; they may be evaluated later only if they materially reduce operational cost without creating a second compute authority.

## 5. Durable application state — Cloud SQL PostgreSQL

SQLite remains a reference/test store only. PostgreSQL owns Runtime records that require durable multi-process transactional semantics, including operational state/events, workflow ownership, authoritative completion, stable finalization, broker idempotency/effect state, reconciliation, and parent-task state not already owned by Temporal history.

Runtime v2 currently has ordered physical migrations through engineering schema v3:

- v1: base durable Runtime tables;
- v2: parent-task row revision/dependency state and reconciliation retry reservations;
- v3: durable completion signing `key_id`.

### Storage compatibility contract

Physical migration version and application compatibility are intentionally separate.

Each Runtime binary declares:

- a minimum supported physical schema version; and
- an explicit set of supported storage compatibility epochs.

Rules:

- additive **EXPAND** migrations may advance physical schema version without advancing compatibility epoch;
- older and newer PINNED Worker Versions may coexist when both support the current compatibility epoch;
- Runtime startup fails closed if the database is below its minimum physical version or the current compatibility epoch is unsupported;
- migration verification itself remains exact and fail-closed;
- a destructive **CONTRACT** migration may advance compatibility epoch only after direct Temporal readback proves every incompatible older Worker Version is safely Drained/non-serving;
- do not weaken this to “accept any newer schema”. Compatibility must remain explicit.

This contract prevents a long-lived pinned workflow from becoming permanently stuck merely because the physical database schema advanced during its wait.

Real Cloud SQL migration, concurrency evidence, backup/PITR evidence, and isolated restore drill remain Deployment gates.

## 6. Runtime identity model

Do not treat every API and Worker release field as one cross-role identity.

Runtime v2 separates three identities:

### Shared Runtime Compatibility Identity

Contains only values that must genuinely agree across compatible API/Worker processes, including:

- environment and region;
- durable database identity;
- storage compatibility contract/epoch;
- Temporal endpoint/namespace/task queue;
- shared deployment-owned provider/Authority configuration fingerprints where applicable.

### API Release Identity

Contains API-specific release properties, including:

- API release/deployment identity;
- public MCP URL and Host/Origin policy;
- OAuth resource-server configuration;
- completion-attestation generation/key identity;
- API-specific secret/config bindings.

### Worker Version Identity

Contains Worker-version-specific properties, including:

- Temporal Worker Deployment/Build ID;
- immutable worker image digest;
- Worker release identity;
- version-specific capacity and storage compatibility declaration.

A new API release must be able to coexist with an older compatible pinned Worker Version. Worker Build ID therefore must not be an API/Worker shared equality invariant.

## 7. Temporal rollout admission

A normal Worker rollout sequence is:

1. materialize candidate Worker compute without deleting older versions;
2. start candidate capacity;
3. direct-read Temporal Worker Version and expected task queues;
4. require pollers on every expected task queue;
5. only then set/ramp the candidate version;
6. verify workflow/activity health and Runtime evidence;
7. promote Current only after admission evidence passes;
8. retain prior version while it is Active/Draining;
9. require repeated direct Drained evidence before scale-to-zero/retirement;
10. preserve rollback/recovery path for pinned workflows.

Deployment automation should use a single authorized Temporal routing manager identity so competing writers cannot independently change Current/Ramping state.

## 8. Secret ownership

Selected baseline: Google Secret Manager.

Production requirements:

- no secret values in source control;
- service identities receive only role-required Secret Accessor permissions;
- secret-backed configuration binds immutable numeric Secret Manager versions, not moving aliases;
- completion signing keys carry explicit key IDs;
- previous completion keys are verification-only and remain bounded/revocable;
- password-bearing database URLs require immutable secret binding;
- startup fails closed if required secrets or bindings are unavailable.

`HAO_DATABASE_URL` need not be secret-bound when it contains no password; Runtime must not pretend passwordless/IAM DB auth is wired unless deployment evidence proves it.

## 9. OAuth / Hao identity

Runtime MCP is an OAuth Resource Server, not an Authorization Server.

Scopes remain layered:

- `hao:access` — global MCP access;
- `hao:read` — per-tool read permission;
- `hao:execute` — per-tool execution permission;
- `hao:approve` — per-tool approval permission.

OAuth identity is not consequential-action approval. Hao-origin authorization must remain bound to the exact current run/action scope and current trusted policy/Authority context.

The actual ChatGPT/private custom-MCP consumer capability is a separate Natural-use gate and must be tested on the real account/surface; infrastructure deployment success must not be treated as proof of consumer ingress compatibility.

## 10. Decision provenance and completion traceability

Runtime-owned decision provenance is part of the deployment contract.

Current engineering Phase A mints runtime-owned `policy_fingerprint` and `decision_id` from trusted policy, Authority snapshot, run/action, Mode/TASK, operational version, and trusted resolution context. Model/caller input does not own these fields.

Remaining Phase B requirement:

- admission result, provider execution/verification, observability events, authoritative completion attestation, and durable completion persistence must remain traceable to the same runtime-owned decision identity;
- caller/model must not be able to forge or replace that identity;
- migration must be backward-compatible and preserve existing durable completion semantics.

Phase A Engineering PASS is not full Decision Provenance completion until Phase B evidence exists.

## 11. Terraform / GitHub deployment security

Selected deployment direction remains GitHub Actions OIDC -> Google Workload Identity Federation -> short-lived Google credentials -> Terraform.

The bootstrap currently proves repository/owner/ref-scoped WIF engineering, but production credential admission still requires further hardening:

- privileged service-account impersonation must be constrained by exact trusted workflow identity and, for apply, an approved GitHub Environment or equivalent protected approval boundary;
- a shell branch check is not sufficient as the only credential-level control;
- PLAN and APPLY authorization should be separable even when they reuse one WIF pool/provider;
- no long-lived Google service-account key by default.

### Saved-plan protocol

Real Terraform plan/apply remains an open deployment-plane carrier. The intended protocol is:

1. credentialed `terraform plan -out=<saved-plan>` against the exact protected backend;
2. compute plan digest and bind Git SHA, target identity, tool/provider lock identity, and plan-run ID;
3. store the binary plan in protected private storage, not a public GitHub artifact;
4. expose only sanitized review output;
5. bind Hao approval to the exact plan manifest/digest;
6. APPLY downloads the exact saved object generation, re-verifies digest/context, and runs `terraform apply <saved-plan>`;
7. provider direct readback follows apply;
8. bounded lifecycle cleanup removes stale plan objects.

Saved Terraform plans and machine-readable plan output may contain sensitive values and must not be published as ordinary public Actions artifacts.

## 12. Deployment-owned application contracts

The model must not author deployment-owned target/safety configuration.

Current controlled Google Sheets path uses:

- `HAO_SHEETS_TARGETS_JSON` — exact binding ID, spreadsheet ID, A1 range, write option, Authority sources;
- `HAO_TASK_POLICIES_JSON` — TASK acceptance criteria, Authority sources, gates, Hao-acceptance and assurance-tag rules;
- `HAO_PARENT_TASK_PLANS_JSON` — parent plan, TASK identity, child slots, capabilities, trusted bindings, authorization targets, parent gates.

A brand-new database additionally requires deployment-owned first-boot Mode/TASK values. They must not overwrite existing durable state on restart.

Critical shared invariants include:

```text
OAuth resource == public MCP URL
OAuth audience == public MCP URL
public MCP hostname in explicit Host allowlist
production public/auth URLs use HTTPS
production persistence is PostgreSQL
storage physical version >= binary minimum
current storage compatibility epoch is explicitly supported
required secrets/bindings exist
current completion key id is non-empty and durable
provider target / task policy / parent plan config parses successfully
parent plan TASK == current Runtime TASK at parent start
model cannot author runtime-owned target/binding/decision identity
API and Worker share only true compatibility invariants
Worker Build ID remains Worker-version-specific
Temporal promotion requires expected poller/task-queue evidence
```

Malformed required configuration is a startup/admission failure, not a warning.

## 13. Deliberately not selected

- self-hosted Temporal as the initial baseline;
- SQLite as production state;
- disabling MCP DNS-rebinding protection;
- one all-powerful OAuth token;
- model-supplied reconciliation evidence or blind replay of `UNKNOWN_EFFECT`;
- experimental Temporal Serverless Workers as an initial dependency;
- Cloud Deploy or another overlapping deployment authority without explicit migration;
- automatic deletion of old Worker Pools solely because a Terraform input disappeared;
- indefinite legacy/native critical execution.

## 14. Deployment gates before real traffic

Production is not ready until all applicable gates pass with direct evidence:

1. Runtime production config/startup validation.
2. Real Streamable HTTP Host/Origin security.
3. Real OAuth positive/negative E2E and exact Hao subject/scope checks.
4. Cloud SQL migration/concurrency plus backup/PITR and isolated restore drill.
5. Storage expand/contract compatibility evidence across multiple pinned Worker Versions.
6. Temporal restart/resume/signal/version-routing and expected-poller/task-queue evidence.
7. Worker local startup/liveness evidence independent from Temporal connectivity.
8. Completion signing key rotation/replay/revocation evidence.
9. Decision Provenance Phase B end-to-end completion traceability.
10. OpenTelemetry correlation without model payload/expected-state leakage.
11. First real Authority adapter and first real provider mutation adapter with receipt/readback/idempotency/reconciliation.
12. No uncontrolled/native result can update authoritative completion.
13. Natural-use evidence demonstrates lower correction/recovery burden than the legacy path.

## 15. Current execution status

Implemented and engineering-verified in the EXP lineage includes:

- Runtime v2 core execution/admission/completion contracts;
- Streamable HTTP MCP and OAuth resource-server boundary;
- durable PostgreSQL migration baseline through physical schema v3;
- explicit storage compatibility contract for multi-version PINNED coexistence;
- split shared/API/Worker identity model;
- Temporal PINNED Worker Versioning assembly;
- `0..N` Worker Build-ID keyed Cloud Run Worker Pool Terraform model with scale-zero retention;
- deterministic Temporal rollout policy gates for capacity/poller/task-queue/Drained conditions;
- provider receipt/readback/idempotency/`UNKNOWN_EFFECT` reconciliation boundaries;
- parent-task durable coordination and restart semantics;
- Decision Provenance Phase A;
- OpenTelemetry-compatible Runtime events;
- role-scoped production secret/config validation;
- dual Runtime v2 and legacy regression gates;
- Terraform fmt/init/validate/mocked-test engineering gates.

Still not proven/executed:

- exact GCP target project selection/readback and authenticated provider control surface;
- GCS/WIF/deployer bootstrap apply;
- credentialed real-project Terraform plan/apply;
- workflow/environment-level WIF privilege hardening;
- Artifact Registry publication and actual Cloud Run/Worker Pool deployment;
- real Worker local health probe deployment evidence;
- Cloud SQL production migration/PITR/restore drill;
- Secret Manager production values/rotation;
- Temporal Cloud namespace/routing mutations and live multi-version drain evidence;
- real OAuth IdP configuration and actual ChatGPT consumer ingress;
- Decision Provenance Phase B;
- real Workspace/GitHub provider cutover;
- natural-use production traffic;
- PR merge, production cutover, and legacy critical-path decommission.

No item in the second list may be described as completed without direct provider/deployment/readback evidence. CI PASS is Engineering PASS only.
