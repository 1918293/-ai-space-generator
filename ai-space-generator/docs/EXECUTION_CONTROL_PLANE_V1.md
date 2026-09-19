# Hao System Execution Control Plane v1 — EXP Target Architecture

## 1. Problem statement

The recurring Hao System failure is not primarily missing knowledge or missing rules. Correct rules, Authority, capability evidence, and historical regressions often already exist, yet the active model can still fail to apply them when choosing actions, interpreting tool results, mutating state, or declaring completion.

The structural problem is overlapping ownership: the same probabilistic model may act as planner, action selector, executor coordinator, observer, verifier, state updater, and completion announcer. Natural-language governance is therefore advisory unless a deterministic runtime makes it impossible to advance without satisfying executable preconditions and postconditions.

This architecture moves **transition ownership** out of free-form model behavior.

## 2. Design decision

The previous bounded idea of hardening only Mode rendering and formal-record persistence is not sufficient as the long-term target. Those are important regressions, but they are instances of a broader execution-control problem.

The target is a dedicated **Execution Control Plane** for any work whose correctness depends on state transitions, external side effects, durable persistence, cross-session continuity, or verifiable completion.

Native conversational reasoning remains useful, but it does not own authoritative execution transitions.

## 3. Two execution paths

### Advisory path

Use native conversational reasoning for:

- explanation and discussion;
- brainstorming and open exploration;
- read-only synthesis where no durable state or completion guarantee is claimed;
- recommendation generation before a controlled action is admitted.

Outputs from this path are not execution receipts and cannot independently establish `EXECUTED`, `PERSISTED`, `VERIFIED`, `ACCEPTED`, or `COMPLETED`.

### Controlled path

Mandatory for:

- any mutation of Hao System Authority or operational state;
- external publication, messaging, permissions, deletion, financial or irreversible effects;
- execution where a result may later be claimed as completed;
- cross-session active Mode / Task operational state;
- high-value workflows where historical regressions show silent or plausible failure;
- actions that require tool capability, binding, authorization, readback, or acceptance gates.

## 4. Plane separation

### A. Interaction / Projection Plane

ChatGPT or another client presents the conversation, status, Mode/TASK header, summaries, maps, Handoff projections, and human approval requests.

It is a **view**, not operational Authority. Rendering cannot modify execution state.

### B. Model / Reasoning Plane

The LLM may:

- interpret intent;
- propose structured goals, plans, and actions;
- select among eligible options;
- explain failures and alternatives.

The LLM may not directly:

- advance run phase;
- authorize its own side effects;
- convert tool success into verified success;
- mark persistence complete without receipts;
- mark Hao acceptance;
- emit an authoritative `CLOSED` state.

### C. Execution Control Plane

Deterministic owner of:

- active run state;
- legal phase transitions;
- action contract admission;
- Action Externality classification and authorization checks;
- current Authority references required by an action;
- retry admission and no-delta retry blocking;
- failure attribution;
- evidence floors for claims;
- completion firewall;
- deterministic Mode/TASK rendering inputs.

Canonical run path:

`RESOLVED -> ADMITTED -> EXECUTING -> OBSERVED -> VERIFIED -> COMMITTED/CLOSED`

Failure paths are explicit:

`BLOCKED`, `FAILED`, `AWAITING_HAO`.

No free-form model output can skip these transitions.

### D. Tool Execution / Broker Plane

All side-effecting controlled tools must be invoked behind a broker or function-tool boundary owned by the runtime.

The broker is responsible for:

- provider/action binding;
- input schema validation;
- exact authorization scope;
- idempotency key propagation;
- timeout and retry behavior;
- provider receipt capture;
- readback hooks;
- rollback/compensation metadata when available.

Read-only hosted tools may remain outside the broker when they cannot cause a controlled state transition. Their output is evidence input only until independently admitted and verified.

### E. State / Evidence Plane

Authority is partitioned by fact class to avoid split-brain ownership.

| Fact class | Authoritative owner |
| --- | --- |
| Hao durable semantic decisions, project structure, formal governance | Existing Google Drive Hao System canonical Authority |
| Active run phase, in-flight action, authorization wait, retry/failure state | Execution runtime durable state |
| External system state | The external provider, verified by direct readback/receipt |
| Tool/provider current availability | Live provider observation; session/environment scoped |
| Handoff, XMemo pointer, summaries, maps, dashboards | Derived projections only |
| Completion claim | Execution Control Plane from evidence floor |
| Hao acceptance | Explicit Hao input only |

The same fact class must not have two writers.

## 5. Durable orchestration

The long-term implementation should use a durable workflow runtime rather than an in-memory agent loop.

Recommended production mapping:

- **Temporal**: durable workflow state, replay, retries, timers, signals, human approval waits, long-running recovery;
- **OpenAI Agents SDK / Responses API**: model reasoning and structured action proposal;
- **custom function tools / MCP adapters behind the broker**: controlled external actions;
- **OpenTelemetry**: transition/tool/evidence traces without storing private chain-of-thought;
- **Google Drive Hao System**: existing durable semantic Authority, not the workflow engine;
- **GitHub**: executable contracts, regression tests, code review, CI evidence.

Temporal already maintains an OpenAI Agents SDK integration where orchestration is durable and model/tool I/O can be Activities. This is a stronger fit than continuing to emulate durability in Sheets or conversation state.

OPA or another external policy engine is **not required initially**. Keep policy as typed deterministic code until policy complexity or multi-service reuse produces a real need for policy-as-code separation.

## 6. Controlled action contract

Every controlled action must carry at least:

- run ID / action ID;
- stable TASK and resolved Mode;
- goal validity;
- relevant acceptance criteria;
- current Authority references / snapshot tokens;
- action archetype;
- capability;
- provider + action binding;
- externality;
- exact authorization scope when required;
- expected state delta for mutations;
- idempotency key for mutation/publication;
- rollback/compensation capability where known;
- required evidence kinds;
- failure stage + code when unsuccessful.

A model may propose this structure. The controller validates it.

## 7. Action Externality

Initial stable classes:

- `READ_ONLY`
- `PRIVATE_REVERSIBLE`
- `PRIVATE_IRREVERSIBLE`
- `EXTERNAL_REVERSIBLE`
- `EXTERNAL_IRREVERSIBLE`
- `FINANCIAL_PERMISSION_OR_SECURITY`

Explicit Hao authorization is required for irreversible, external, financial, permission, and security-sensitive effects.

Authorization belongs to the **specific action scope**, not the provider as a whole.

## 8. Failure attribution

A failure must become a typed control-plane state before the model explains it.

Stable pipeline stage examples:

- `INTENT`
- `AUTHORITY`
- `PLAN`
- `POLICY`
- `ROUTING`
- `BINDING`
- `TOOL_INPUT`
- `TOOL_EXECUTION`
- `TOOL_OUTPUT`
- `SEMANTIC`
- `MUTATION`
- `VERIFICATION`
- `COMPLETION`
- `PROJECTION`

These are pipeline locations, not a closed global taxonomy of every failure mechanism. `failure_code` and mechanism remain extensible.

The model explains the typed failure; it does not invent the failure state.

## 9. Completion Evidence Floor

Claim strength may never exceed direct evidence strength.

Minimum examples:

- `EXECUTED` -> direct tool/provider receipt;
- `PERSISTED` -> tool receipt + direct state readback + verification pass;
- `VERIFIED` -> executable/direct verification receipt;
- `ACCEPTED` -> explicit Hao acceptance only;
- `COMPLETED` -> verification pass + task-specific acceptance gate pass.

A tool returning success is not sufficient for persistence or task completion.

A verification pass for one claim does not prove another claim.

Missing evidence produces an honest verified stall or blocker, never narrative completion.

## 10. Persistence / external mutation protocol

For controlled mutations:

1. resolve the current target identity and Authority snapshot;
2. admit action and authorization;
3. persist action intent in durable run state;
4. execute with idempotency key;
5. capture provider receipt;
6. read back actual resulting state;
7. execute claim-specific verification;
8. record typed evidence receipts;
9. only then emit persistence/completion claims;
10. selectively write material durable semantic deltas back to Hao System Authority.

If the external effect succeeds but canonical persistence fails, the state must remain explicitly `UNSYNCED`/failed-at-persistence rather than claiming global completion. Recovery retries must be idempotent or compensating.

## 11. Projection boundary

Handoff, XMemo, maps, architecture diagrams, dashboards, summaries, and ChatGPT headers are projections.

A projection may display Current state but does not become Current state merely by displaying it.

Projection rendering must consume resolved control/Authority state and cannot feed a contradictory state back into execution without re-admission.

## 12. Historical regression conversion

Past Hao System regressions should be converted into seam-level integration tests, not more natural-language rules.

Required regression families before production cutover:

1. Active Mode state -> render mismatch;
2. `紀錄` routed to ChatGPT Memory instead of Hao System formal persistence;
3. stored project hierarchy not admitted before representation;
4. Exact masked edit silently replaced with full generation;
5. stale XMemo/Handoff projection overriding live Current Authority;
6. Google Sheets/API mutation success but wrong target/row state;
7. source identity / concurrency mismatch such as R65;
8. known false-completion rule stored but same completion failure recurring;
9. provider/action technical success with semantically irrelevant result;
10. search/index miss interpreted as content absence.

The success criterion is not that the controller can describe these failures. It must make the invalid transition impossible or force a loud typed failure.

## 13. Migration strategy

This is a systemic migration, not an indefinite pilot loop.

### Stage 1 — deterministic core

Implement action contracts, run phases, authorization, evidence floors, failure attribution, no-delta retry, completion firewall, and deterministic render inputs.

Status: implemented in this EXP branch with CI contracts.

### Stage 2 — durable controlled runner

Move the action loop into durable orchestration. The workflow owns phase changes; model calls and external I/O become controlled Activities.

### Stage 3 — first-class operational state ownership

Move active Mode/TASK/run operational state out of free-form ChatGPT/Handoff text into the runtime state owner. Handoff becomes derived continuation projection.

Google Drive remains durable semantic Authority for Hao decisions/governance; it is no longer used as an ad-hoc in-flight workflow engine.

### Stage 4 — tool broker cutover

Wrap side-effecting Drive/GitHub and other high-value actions behind runtime-controlled adapters. Side-effecting paths that bypass the broker cannot produce authoritative completion.

### Stage 5 — natural-use cutover

Route qualifying Hao tasks to the controlled path automatically. Advisory chat remains native. Controlled workflows must survive restart, approval waits, provider failure, and cross-session continuation.

### Stage 6 — production acceptance

Production cutover requires:

- all historical regression families above blocked or loudly attributed;
- durable restart/replay test pass;
- external side-effect approval test pass;
- provider failure/fallback test pass;
- mutation/readback/idempotency test pass;
- no false `PERSISTED`, `VERIFIED`, `ACCEPTED`, or `COMPLETED` claims in the acceptance corpus;
- Handoff/projection cannot override runtime/semantic Authority ownership;
- clear rollback to native advisory mode without corrupting canonical Hao System data.

## 14. What this architecture deliberately changes

This proposal **does** reconsider earlier minimalism where that minimalism left the root cause intact.

It changes:

- critical transition ownership from model-authored to runtime-authored;
- active execution state from conversational text to durable control state;
- side-effect tool calls from direct model discretion to brokered actions;
- completion from narrative judgment to evidence-gated transition;
- regressions from mainly stored knowledge to executable seam tests.

It does **not** require:

- moving all Hao System knowledge out of Google Drive;
- creating another semantic knowledge Authority;
- replacing ChatGPT as the conversational/reasoning interface;
- recording chain-of-thought;
- making every read-only exploration a heavy workflow;
- hardcoding every provider or every possible failure type.

## 15. Primary architectural acceptance statement

The redesign succeeds only when this is true in implementation:

> Correct behavior is no longer merely something the model has been told. For controlled work, invalid state transitions, unauthorized actions, unsupported completion claims, and silent unverified success are structurally rejected by the execution system.
