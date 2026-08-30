# Hao System Execution Runtime v2 — EXP System Redesign

## Status

- Mode: EXP
- Role of this branch: Runtime v2 migration seed, not a patch to the legacy ChatGPT-self-governed execution model.
- Semantic Authority remains in existing Hao System canonical sources.
- Production cutover is NOT complete until the acceptance conditions in this document are met.

## 1. Decision

Hao System will stop treating native conversational execution as the authoritative execution model for reliability-critical work.

The redesign preserves accumulated Hao System knowledge, project structure, evidence, decisions, and accepted semantic Authority, while replacing the execution ownership model.

The target is:

```text
Hao
  -> Interaction / Reasoning Client
  -> Hao Runtime v2
       -> Operational State
       -> Task Policy / Authority Resolver
       -> Action Catalog
       -> Authorization
       -> Durable Workflow
       -> Tool Broker
       -> Provider
       -> Readback / Verification
       -> Completion Authority
       -> Audit / Projection
```

Native/direct tool execution may still physically occur outside Runtime v2 while migration is incomplete, but it is non-authoritative and cannot establish Hao System execution or completion state.

## 2. Root cause being removed

The principal historical failure pattern is not lack of rules. It is overlapping ownership inside a probabilistic model runtime.

The legacy model lets one conversational agent partially overlap these roles:

- intent interpreter;
- planner;
- Mode/TASK resolver;
- Authority selector;
- tool selector;
- safety/externality classifier;
- action initiator;
- observer;
- verifier;
- state updater;
- completion announcer.

This allows `Known != Applied` even when the rule and evidence already exist.

Runtime v2 removes this overlap by making critical transitions application-owned and deterministic.

## 3. Systems of authority

Each fact class has one authoritative writer.

| Fact class | Authoritative owner |
| --- | --- |
| Hao durable semantic decisions, projects, relations, accepted governance | Hao System semantic Authority (existing canonical stores) |
| Active Mode/TASK and operational version | Runtime operational-state owner |
| In-flight run phase, action, approvals, retries, failures | Durable workflow runtime |
| Provider side effect | Provider itself, proven by receipt/readback |
| Action safety metadata / externality / assurance | Runtime-owned Action Catalog |
| Task acceptance / required Authority / invariants | Trusted Task Policy / Authority Resolver |
| Completion state | Runtime Completion Authority |
| Hao acceptance | Explicit Hao-origin event |
| Handoff/XMemo/graphs/dashboards/chat headers | Projection only |

A projection is never allowed to become an authoritative writer for its source fact class.

## 4. Execution domains

Runtime v2 divides work by consequence, not by whether a tool happens to be available.

### 4.1 Advisory domain

Native conversational execution is allowed for non-authoritative work such as:

- explanation;
- brainstorming;
- open research;
- public web reading;
- drafting;
- non-authoritative analysis;
- recommendations before controlled execution.

Advisory output cannot independently establish `EXECUTED`, `PERSISTED`, `VERIFIED`, `ACCEPTED`, or `COMPLETED`.

### 4.2 Controlled domain

Runtime v2 is mandatory for any work that can change Hao System truth or create material external consequences, including:

- formal record/persistence;
- project/current/governance mutation;
- GitHub/Drive mutation used as authoritative work;
- messaging/publication;
- permission/security changes;
- destructive or irreversible actions;
- workflows whose completion will be relied on later;
- long-running/cross-session workflows;
- tasks with historical silent-failure regressions;
- any task explicitly marked controlled by trusted policy.

### 4.3 Uncontrolled-effect domain

If a consequential effect occurs outside Runtime v2:

```text
external effect observed
  -> UNCONTROLLED_EFFECT
  -> NON_AUTHORITATIVE
  -> RECONCILIATION_REQUIRED
```

It cannot be promoted to authoritative completion merely because the effect appears successful.

## 5. Runtime-owned transition chain

For controlled work, the legal chain is:

```text
Resolve operational context
  -> Resolve trusted task policy
  -> Resolve Authority snapshot
  -> Resolve trusted action binding
  -> Admit action
  -> Obtain exact authorization when required
  -> Revalidate Authority before side effect
  -> Execute through broker
  -> Capture provider receipt
  -> Read back actual resulting state
  -> Verify claim-specific conditions
  -> Evaluate named acceptance gates
  -> Obtain explicit Hao acceptance if required
  -> Mint signed completion attestation
  -> Commit authoritative completion
  -> Project state outward
```

A model may propose intent and explain outcomes. It does not own transitions in this chain.

## 6. Legacy execution decommission policy

Runtime v2 is not successful if the legacy path remains a peer authority indefinitely.

Legacy path:

```text
ChatGPT
  -> directly infer state / choose action / call tool / interpret result / claim completion
```

Decommission progression:

### L0 — Legacy authoritative

Historical state. Native conversational execution can directly influence Current and completion.

### L1 — Shadowed

Runtime v2 observes/validates selected work, but legacy output may still be used. Temporary development-only state.

### L2 — Runtime authoritative

For controlled work, only Runtime v2 receipts and state are authoritative. Native/direct effects are quarantined.

### L3 — Legacy critical path disabled

Critical provider actions are broker-only wherever the hosting/runtime permits. Any remaining native bypass cannot update Hao System authoritative state.

### L4 — Legacy execution retired

No reliability-critical workflow depends on the legacy self-governed execution model.

Production success requires at least L3 for the controlled scope. The long-term target is L4.

## 7. Reuse / Replace / Retire decision for current branch

### Reuse as Runtime v2 core

- `execution_control.py`: typed run phases, evidence floors, failure stages, completion rules.
- `operational_state.py`: durable Mode/TASK ownership and user-only mutation semantics.
- `action_catalog.py`: trusted provider/action metadata outside model authority.
- `idempotent_broker.py`: durable idempotency and `UNKNOWN_EFFECT` semantics.
- `temporal_control.py` / `temporal_client.py`: durable orchestration, signals, restart/attach.
- `authoritative_completion.py`: signed completion attestation and authoritative commit boundary.
- `projection_control.py`: stale/tampered projection rejection.
- historical seam tests: executable regression evidence.

### Replace / expand for production

- `control_gateway.py`: evolve from one-action preparation into Runtime ingress for task workflows.
- `production_execution.py`: evolve from reference facade into application service with production persistence and operational-version policy.
- `mcp_control_bridge.py` / `mcp_control_server.py`: keep as private controlled entry surface, add real HTTP OAuth identity and deployment configuration.
- SQLite reference stores: replace with production transactional persistence before multi-worker production.
- test-only TaskPolicy/Authority providers: replace with real Hao System Authority adapters.
- reference broker provider: replace with explicit production adapters for selected providers.

### Retire as architectural assumptions

- Native ChatGPT as completion authority.
- Handoff/XMemo/chat summary as operational state owner.
- Provider success as task completion.
- Model-authored externality/authorization metadata.
- Periodic reassertion of critical rules as the main enforcement mechanism.
- Indefinite "pilot" where legacy and controlled paths remain equal authorities.

## 8. Multi-action task model

Real Hao tasks are not single tool calls. Runtime v2 must support a parent task workflow with ordered/parallel controlled child actions.

Example:

```text
Parent Task Run
  -> Read Current Authority
  -> Analyze
  -> Mutate
  -> Direct readback
  -> Verify
  -> Persist semantic delta
  -> Update projection
  -> Close parent task
```

Child action success never closes the parent task automatically.

The parent task owns:

- stable TASK identity;
- task-level acceptance gates;
- dependency graph;
- current Authority version set;
- child action IDs;
- unresolved failures;
- Hao decision waits;
- final completion decision.

## 9. Operational-version policy

A run is bound to the operational context under which it was admitted.

If Mode/TASK/policy changes while a run is active, the run may preserve historical execution evidence, but it cannot silently update a newer Current state.

Default v2 rule:

```text
admitted operational version != current operational version at authoritative commit
  -> STALE_OPERATIONAL_CONTEXT
  -> RECONCILIATION_REQUIRED
```

A policy may explicitly allow historical completion without Current mutation, but that completion must remain scoped to the historical operational version.

## 10. Authorization policy

OAuth identity is necessary but not sufficient for consequential Hao decisions.

Authorization chain:

```text
HTTP OAuth identity + required scope
  -> controlled run ownership
  -> exact current action authorization scope
  -> human confirmation / Hao-origin event when required
  -> Temporal approval signal
  -> post-approval Authority revalidation
  -> broker execution
```

A model tool call cannot manufacture Hao authorization.

## 11. MCP / application entry contract

The private MCP surface is an application boundary, not the source of truth.

Model-visible input must never include runtime-owned fields such as:

- Mode/TASK write authority;
- run phase;
- externality classification;
- trusted assurance tags;
- completion state;
- authorization proof;
- runtime signing material.

Streamable HTTP OAuth is the production authentication boundary. In-memory/stdio transports do not prove production OAuth behavior.

## 12. Provider cutover policy

Provider integrations are migrated by consequence and historical failure cost, not by arbitrary smallness.

Priority order:

1. Hao System semantic persistence / Google Drive paths.
2. GitHub mutations and executable validation.
3. Formal record / continuity integrations used by Current.
4. External messaging/publication/permission actions.
5. Other side-effect providers.

For each migrated provider action, production admission requires:

- trusted binding;
- current auth/permission/binding evidence;
- idempotency semantics;
- direct provider receipt;
- direct readback where the claim depends on resulting state;
- typed failure mapping;
- reconciliation path for ambiguous effect;
- executable natural-use regression evidence.

## 13. Reconciliation subsystem

Runtime v2 must treat ambiguity as a first-class state.

Reconciliation handles:

- `UNKNOWN_EFFECT`;
- `UNSYNCED`;
- uncontrolled/native side effects;
- stale operational context;
- stale Authority snapshot;
- provider receipt without matching readback;
- partial multi-action task completion.

Reconciliation may:

- adopt verified external state;
- repair canonical persistence;
- compensate/rollback where valid;
- supersede a stale action;
- require Hao decision;
- mark permanent unresolved discrepancy.

It must never hide ambiguity by re-running a side effect without evidence that the prior effect did not occur.

## 14. Observability and user-view reliability

Runtime v2 engineering metrics are necessary but not sufficient.

Required operational signals include:

- run/task ID;
- operational version;
- Mode/TASK;
- current run phase;
- Authority snapshot/fingerprint;
- action binding;
- provider/action;
- externality;
- approval wait state;
- failed-at stage/code;
- retry/reconciliation basis;
- receipt/readback/verification status;
- authoritative-completion attestation ID;
- uncontrolled-effect count;
- stale-projection rejection count.

Primary reliability outcomes are user-view outcomes:

- false completion rate;
- wrong Current/Authority rate;
- repeated same-family regression rate;
- human correction rate;
- manual recovery burden;
- critical bypass rate;
- lost/duplicated external side effects.

CI green is Engineering PASS only, not System PASS.

## 15. Production architecture

Target deployment:

```text
ChatGPT / other clients
        |
        v
Authenticated MCP / application ingress
        |
        v
Hao Runtime API
  |-- Operational State Store
  |-- Task Policy / Authority Resolver
  |-- Action Catalog
  |-- Completion Authority
  |-- Reconciliation Service
        |
        v
Temporal durable workflows
        |
        v
Tool Broker / Provider Adapters
        |
        v
Drive / GitHub / other providers

Observability: OpenTelemetry-compatible traces/metrics/events
Semantic Authority: existing Hao System canonical stores
Projection: Handoff / XMemo / graphs / summaries / chat UI
```

Production secrets, signing keys, tokens, and OAuth configuration must be outside source control and support rotation/revocation.

## 16. Migration program

### Phase A — Engineering baseline

- Current branch tests green.
- Real Streamable HTTP OAuth negative/positive path tested.
- All existing seam regressions remain green.
- Existing v1 components classified Reuse/Replace/Retire.

### Phase B — Runtime application skeleton

- production-configurable service process;
- Temporal worker/client lifecycle;
- transactional state persistence;
- secret/key management boundary;
- HTTP/MCP endpoint;
- health/readiness checks;
- structured observability.

### Phase C — Real Authority integration

- production TaskPolicyProvider backed by current Hao semantic Authority;
- Authority snapshot/readback adapters;
- projection consumes Runtime/Authority state only;
- operational state no longer restored from stale projection.

### Phase D — Real provider broker integration

- migrate priority provider actions;
- side-effect calls receive idempotency keys and receipts;
- ambiguous effects enter reconciliation;
- bypassed effects remain non-authoritative.

### Phase E — Parent task workflow

- support multi-action tasks;
- task-level acceptance;
- child action dependency tracking;
- restart/cross-session continuity;
- explicit task-level completion authority.

### Phase F — Natural-use controlled cutover

- selected real Hao critical tasks run through Runtime v2;
- legacy path is non-authoritative for that scope;
- measure user-view reliability and correction burden;
- fix false blocks and missed blocks at the actual seam.

### Phase G — Decommission

- expand controlled scope based on real use;
- disable/withdraw legacy critical provider paths where possible;
- enforce Runtime attestation for authoritative completion/persistence;
- retire legacy critical execution semantics.

## 17. Promotion gates

Runtime v2 cannot be promoted merely because code exists or CI is green.

Required before production promotion:

1. HTTP OAuth E2E PASS.
2. Real Hao identity/scopes configured and revocable.
3. Mode/TASK human-authoritative mutation path PASS.
4. Real semantic Authority adapter PASS.
5. At least one real provider mutation path with receipt/readback/idempotency PASS.
6. Multi-action parent task workflow PASS.
7. `UNKNOWN_EFFECT` reconciliation PASS.
8. stale operational context cannot update newer Current.
9. historical regression corpus blocked/loudly failed.
10. native/uncontrolled effect cannot mint authoritative completion.
11. restart/replay/cross-session continuation PASS.
12. security tests for token/scope/ownership/approval/attestation replay PASS.
13. natural-use user-view reliability materially improves over legacy baseline.
14. legacy controlled scope reaches at least decommission state L3.

## 18. Non-goals

Runtime v2 does not require:

- replacing Hao System semantic knowledge;
- migrating every read-only research operation;
- storing model chain-of-thought;
- building another knowledge graph or Handoff authority;
- testing every provider before it is relevant;
- keeping changes artificially small when a system boundary must move.

## 19. Primary acceptance statement

Runtime v2 succeeds only when reliability-critical Hao System work is governed by a system whose execution, evidence, state transitions, and completion cannot be authored solely by the same probabilistic model that proposes the work.

The migration is complete for a scope only when the legacy self-governed execution path has lost authoritative power for that scope.
