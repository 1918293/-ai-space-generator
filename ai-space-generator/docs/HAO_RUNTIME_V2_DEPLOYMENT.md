# Hao System Execution Runtime v2 — Production Topology Decision (EXP)

## Status

This document selects the current production architecture target for Runtime v2. It is an EXP deployment decision, not evidence that any cloud resource, account, DNS name, OAuth tenant, database, or Temporal Cloud namespace has been provisioned.

## 1. Selected topology

```text
ChatGPT / approved clients
        |
        | HTTPS + OAuth 2.1 bearer token
        v
Cloud Run Service — asia-east1 (Taiwan)
Hao Runtime API / Streamable HTTP MCP
        |
        +--------------------+
        |                    |
        v                    v
Cloud SQL PostgreSQL     Temporal Cloud Namespace
asia-east1               Namespace Endpoint + API key
        ^                    |
        |                    v
        |              Cloud Run Worker Pool
        |              asia-east1
        |              Hao Temporal Worker
        |                    |
        +----------+---------+
                   |
                   v
             Tool Broker
                   |
                   v
        Controlled Provider Adapters
        Drive / GitHub / later providers

Secrets: Google Secret Manager
Observability: OpenTelemetry-compatible traces/metrics/events
Semantic Authority: existing Hao System canonical stores
Projection: Handoff / XMemo / graphs / summaries / chat UI
Authorization Server: standards-compliant external OAuth provider; Auth0 is the current implementation candidate, subject to end-to-end ChatGPT/MCP OAuth compatibility validation before production commitment.
```

## 2. Why GCP asia-east1

Runtime v2 should keep the API process, background worker, and transactional application state in one primary region unless a future HA decision intentionally changes that topology.

Current default region: `asia-east1` (Taiwan).

Reasons:

- closest current Google Cloud region to the primary operator;
- Cloud Run supports `asia-east1`;
- Cloud Run Worker Pools support `asia-east1`;
- Cloud SQL for PostgreSQL supports `asia-east1`;
- co-location reduces unnecessary cross-region latency and egress between Runtime API, worker, and database.

This is a deployment decision, not an application invariant. Runtime code must not reject a future disaster-recovery or migration region merely because today's primary is Taiwan.

## 3. Runtime API: Cloud Run Service

The MCP/API ingress is an HTTP workload and belongs on a Cloud Run Service.

Responsibilities:

- `/mcp` Streamable HTTP endpoint;
- OAuth resource-server middleware;
- exact Host/Origin transport security;
- runtime context/status APIs;
- controlled-run submission/finalization;
- deployment-owned parent-task control surface;
- owner-bound reconciliation inspect/resolve/retry-with-changed-delta control surface;
- health/readiness endpoints;
- no provider side effect outside the broker boundary.

Requirements:

- stable HTTPS public hostname;
- explicit MCP `allowed_hosts`; no wildcard disablement of DNS-rebinding protection;
- production configuration validated at startup;
- application service identity with only the permissions needed by Runtime API;
- secrets injected from Secret Manager, never committed to GitHub.

## 4. Durable worker: Cloud Run Worker Pool

Temporal workers are continuous non-HTTP pull workers. Cloud Run Worker Pool is therefore the selected baseline rather than a request-driven Cloud Run Service or scheduled Job.

The worker runs:

- Runtime v2 Temporal workflows;
- tool-broker activities;
- Authority preflight/readback activities;
- verification and reconciliation activities.

Initial production deployment should use an explicitly managed non-zero worker count. Autoscaling or Temporal's newer Serverless Workers integration must not become a production dependency until that feature is beyond pre-release and has its own Hao Runtime acceptance evidence.

## 5. Durable application state: Cloud SQL PostgreSQL

SQLite remains useful for reference tests but is not the production multi-process state store.

PostgreSQL owns application/runtime records that must survive process replacement and be transactionally consistent, including the production equivalents of:

- operational state/events;
- MCP workflow ownership bindings;
- authoritative completion records;
- stable finalization issuance reservations needed for response-loss/restart idempotency;
- durable broker idempotency/effect state;
- reconciliation records;
- parent task/application state not already owned by Temporal workflow history.

Runtime v2 must define explicit database migrations and uniqueness/locking semantics before cutover.

## 6. Workflow durability: Temporal Cloud

Selected baseline: managed Temporal Cloud rather than operating a self-hosted Temporal cluster.

Reasons:

- Runtime v2 requires durable waits, restart/resume, signals, and workflow history;
- self-hosting Temporal would introduce a second reliability platform that Hao System itself would have to operate;
- the managed service reduces operational burden while preserving the required workflow semantics.

Connection policy:

- use the Namespace Endpoint rather than hardcoding a regional endpoint;
- store API key in Secret Manager;
- rotate credentials without code changes;
- do not use Temporal Activity automatic retries for consequential broker actions unless Runtime v2 has explicitly classified the operation retry-safe;
- retain Runtime v2 idempotency/`UNKNOWN_EFFECT` semantics even when Temporal itself is healthy.

## 7. Secret ownership

Selected baseline: Google Secret Manager.

Secrets include at least:

- completion-attestation signing secret/key material;
- Temporal Cloud API key;
- OAuth verifier/JWKS or introspection credentials when needed;
- database credential or connector secret where workload identity is not used;
- provider credentials that cannot use workload identity.

Requirements:

- no secret values in source control;
- production service identity receives only Secret Accessor permissions for the secrets it needs;
- signing keys carry explicit key IDs and rotation history;
- startup fails if required secrets are unavailable.

## 8. OAuth / identity decision

Runtime MCP is an OAuth Resource Server, not an Authorization Server.

Current architecture:

```text
Authorization Server
    -> authenticates Hao
    -> issues token for Runtime v2 resource/audience
    -> scopes
       hao:access
       hao:read
       hao:execute
       hao:approve

Runtime MCP Resource Server
    -> verifies signature/introspection
    -> validates issuer
    -> validates resource/audience
    -> validates expiry
    -> validates global hao:access
    -> maps subject
    -> application policy verifies exact Hao subject
    -> each tool verifies its own read/execute/approve scope
```

Important separation:

- `hao:access` is the only global MCP middleware scope.
- `hao:read`, `hao:execute`, and `hao:approve` are per-tool application permissions.
- A read token must not require or imply `hao:approve`.
- An approve-only operation must not change durable state and then fail its response merely because a hidden `hao:read` check was added afterward.
- OAuth identity is not itself a consequential-action approval; exact action approval remains a Hao-origin human confirmation bound to current run scope.
- Parent-task Hao acceptance and reconciliation dispositions also require human confirmation at their own control boundaries.

Auth0 is currently the leading implementation candidate because it supports custom API audiences and scopes and standard OAuth authorization flows. It is not yet production-selected until the actual ChatGPT/private MCP connection completes end-to-end with the required client registration/authorization behavior. Runtime interfaces remain provider-neutral so a standards-compatible IdP can replace it without changing execution semantics.

## 9. Production configuration identity

API and worker must load the same deployment identity, including:

- environment;
- runtime role;
- region;
- public MCP resource URL;
- explicit MCP Host/Origin allowlists;
- PostgreSQL URL;
- Temporal endpoint/namespace/task queue;
- OAuth issuer/resource/audience;
- expected Hao subject;
- attestation key ID;
- observability endpoint;
- deployment-owned provider target catalog through `HAO_SHEETS_TARGETS_JSON`;
- trusted task policies and Authority source bindings through `HAO_TASK_POLICIES_JSON`;
- deployment-owned multi-action parent plans through `HAO_PARENT_TASK_PLANS_JSON`.

The three JSON contracts are application configuration, not model-authored instructions. For the currently implemented Google Sheets controlled path:

- `HAO_SHEETS_TARGETS_JSON` owns the exact binding ID, spreadsheet ID, A1 range, write option, and Authority file sources;
- `HAO_TASK_POLICIES_JSON` owns TASK acceptance criteria, Authority sources, required gates, Hao-acceptance requirement, and required/forbidden action assurance tags;
- `HAO_PARENT_TASK_PLANS_JSON` owns parent `plan_id`, exact TASK identity, child slot IDs, requested capabilities, trusted binding IDs, authorization targets, parent gates, and parent Hao-acceptance requirement.

Critical invariants:

```text
OAuth resource == public MCP URL
OAuth audience == public MCP URL
public MCP hostname in explicit Host allowlist
production public/auth URLs use HTTPS
production persistence is PostgreSQL
required secrets exist and signing secret meets minimum strength
provider target / task policy / parent plan config exists and parses successfully
parent plan TASK == current runtime TASK at parent start
model cannot author target spreadsheet/range or parent child capability/binding
API and worker use the same database identity
API and worker use compatible provider target configuration for the controlled provider path
```

Missing or malformed required deployment-owned configuration is a startup failure, not a warning. A configuration failure must not be repaired by allowing the model to supply runtime-owned target, binding, Authority, safety, or completion fields.

## 10. What is deliberately not selected

### Not selected: self-hosted Temporal

Reason: creates avoidable platform-operational responsibility for a system whose purpose is to improve reliability.

### Not selected: SQLite production state

Reason: reference/single-process semantics are insufficient for API/worker multi-process deployment and long-lived authoritative state.

### Not selected: disabling MCP DNS-rebinding protection

Reason: a real hostname is not justification for removing the boundary; exact Host allowlisting is available.

### Not selected: all-powerful OAuth token

Reason: global read+execute+approve scopes collapse the authorization layers. Runtime uses base access + per-tool scopes instead.

### Not selected: model-supplied reconciliation evidence or blind replay

Reason: `UNKNOWN_EFFECT` means the prior side effect may already have occurred. Reconciliation resolution may use only trusted evidence already present in the durable case. Retry-with-delta is allowed only after a retry-safe verified resolution, must change the expected-state delta, creates a new controlled run, and still goes through normal authorization and verification.

### Not selected: Temporal Serverless Workers for GCP Cloud Run as initial production dependency

Reason: the feature is pre-release as of this design checkpoint. The selected baseline uses ordinary Cloud Run Worker Pools until the newer integration has mature production evidence.

### Not selected: indefinite legacy/native critical execution

Reason: Runtime v2 exists to replace that authority model. Native/direct effects remain non-authoritative for controlled scope and must ultimately lose critical-path authority.

## 11. Deployment gates before real traffic

A production environment is not ready until all are true:

1. Runtime config startup validation PASS, including all required deployment-owned target/policy/parent-plan contracts.
2. Streamable HTTP Host/Origin security PASS on the real hostname.
3. OAuth negative and positive E2E PASS against the real Authorization Server.
4. Wrong subject / missing per-tool scope tests PASS.
5. Cloud SQL migrations and transactional concurrency tests PASS, including completion, stable-finalization, parent-task, workflow-ownership, idempotency, and reconciliation records.
6. Temporal worker restart/resume/signal tests PASS against the target namespace.
7. completion signing key rotation/replay tests PASS.
8. OpenTelemetry traces correlate MCP request -> parent/child workflow where applicable -> activity -> provider receipt -> verification -> reconciliation/finalization -> completion without recording model payloads or expected-state contents.
9. first real Authority adapter PASS.
10. first real provider mutation adapter PASS with receipt/readback/idempotency/reconciliation.
11. no uncontrolled/native result can update authoritative completion.
12. natural-use controlled task evidence shows lower correction/recovery burden than the legacy path.

## 12. Current execution status

Designed / implemented in EXP branch:

- Runtime v2 core contracts;
- Temporal workflow prototype;
- MCP server/bridge and explicit HTTP transport allowlist;
- OAuth scope separation, exact subject policy, and post-approval response behavior that does not add a hidden read scope;
- deployment-owned provider target, trusted task policy, and parent-task plan configuration contracts;
- completion attestation plus stable finalization issuance reservation wired into the shared API production path for response-loss/restart retries;
- parent multi-action task coordinator using the same controlled child `ProductionExecutionService` path, shared workflow ownership registry, existing authorization path, restart-safe Postgres state, and `UNSYNCED` reconciliation boundary;
- owner-bound reconciliation inspect/resolve/retry-with-changed-delta MCP controls over the existing durable reconciliation store, with no model-supplied verification evidence and no blind replay of `UNKNOWN_EFFECT`;
- OpenTelemetry-compatible events for direct controlled runs, parent-task controls, reconciliation controls, provider outcomes, and authoritative completion, with regression coverage preventing model payload/delta leakage;
- dual Runtime v2 and legacy application CI gates for engineering regression coverage.

Not executed yet:

- GCP project/resource provisioning;
- Cloud Run deployment;
- Cloud SQL instance/schema migration;
- Secret Manager secrets;
- Temporal Cloud production namespace;
- real Auth0/other IdP configuration;
- DNS/TLS hostname;
- real Drive/GitHub broker adapter production cutover;
- natural-use production traffic;
- legacy critical-path decommission.

No item in the second list may be described as completed until direct deployment/readback evidence exists. CI PASS is Engineering PASS only; it is not Deployment PASS, Natural-use PASS, or System PASS.
