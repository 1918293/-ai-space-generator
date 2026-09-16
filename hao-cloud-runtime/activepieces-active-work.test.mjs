import assert from 'node:assert/strict';
import {
  ACTIVEPIECES_ACTIVE_WORK_POLICY,
  ACTIVEPIECES_SIGNAL_VERSION,
  buildPreflightFromActivepieces,
  normalizeActivepiecesRun,
  parseHaoTags,
  resolveActivepiecesSignal,
} from './activepieces-active-work.mjs';

const NOW = Date.parse('2026-09-17T03:00:00+08:00');
const PROJECT = 'projSynthetic000000001';
const FLOW = 'flowSynthetic000000001';

function run({
  id,
  status,
  runKey = 'RUN-A',
  objective = 'OBJ-A',
  target = 'TARGET-A',
  lane = 'SYNTHETIC',
  verified = false,
  blockerFingerprint = null,
  updated = '2026-09-17T02:59:30+08:00',
} = {}) {
  const tags = [
    `hao:run-key=${runKey}`,
    `hao:objective=${objective}`,
    `hao:target=${target}`,
    `hao:lane=${lane}`,
  ];
  if (verified) tags.push('hao:verified=true');
  if (blockerFingerprint) tags.push(`hao:blocker-fingerprint=${blockerFingerprint}`);
  return {
    id: id ?? `provider-${runKey}`,
    projectId: PROJECT,
    flowId: FLOW,
    status,
    tags,
    created: updated,
    updated,
    startTime: updated,
    finishTime: status === 'SUCCEEDED' ? updated : null,
  };
}

const baseCandidate = {
  runKey: 'RUN-B',
  objective: 'OBJ-B',
  target: 'TARGET-B',
  expectedDeltaOverlap: false,
  targetConflict: false,
};

const basePreflight = {
  action: 'SYNTHETIC_ACTION',
  freshStateRead: true,
  currentStateResolved: true,
  targetIdentityVerified: true,
  consequential: true,
  necessityEstablished: true,
  currentFingerprint: 'v1',
  desiredFingerprint: 'v2',
  validationPlanReady: true,
  selectedValidationEvidence: ['SYNTHETIC_OR_ADVERSARIAL_MATRIX', 'PROVIDER_READBACK'],
};

const options = { nowMs: NOW, staleAfterMs: 15 * 60 * 1000 };

assert.equal(ACTIVEPIECES_SIGNAL_VERSION, '0.1.0-exp');
assert.equal(ACTIVEPIECES_ACTIVE_WORK_POLICY.providerRole, 'ACTIVE_WORK_SIGNAL_ONLY');
assert.equal(ACTIVEPIECES_ACTIVE_WORK_POLICY.authority, 'NONE');
assert.equal(ACTIVEPIECES_ACTIVE_WORK_POLICY.noGlobalTaskDatabase, true);
assert.equal(ACTIVEPIECES_ACTIVE_WORK_POLICY.formalMutationStillRequiresSingleWriteGateway, true);

assert.deepEqual(
  parseHaoTags(['other:x=y', 'hao:run-key=R1', 'hao:verified=true']),
  { 'run-key': 'R1', verified: 'true' }
);

const cases = [
  {
    name: 'empty provider run list permits fresh consequential work',
    runs: [],
    candidate: baseCandidate,
    expected: 'PROCEED',
  },
  {
    name: 'same objective active run waits instead of duplicate execution',
    runs: [run({ status: 'RUNNING', runKey: 'RUN-A', objective: 'OBJ-B', target: 'TARGET-X' })],
    candidate: baseCandidate,
    expected: 'WAIT_ACTIVE_WORK_SAME_OBJECTIVE',
  },
  {
    name: 'identical RUN_KEY active run waits',
    runs: [run({ status: 'QUEUED', runKey: 'RUN-B', objective: 'OLD-OBJ', target: 'TARGET-X' })],
    candidate: baseCandidate,
    expected: 'WAIT_ACTIVE_WORK_SAME_OBJECTIVE',
  },
  {
    name: 'upstream dependency active run waits',
    runs: [run({ status: 'PAUSED', runKey: 'UPSTREAM-1', objective: 'OBJ-X', target: 'TARGET-X' })],
    candidate: { ...baseCandidate, dependsOnRunKeys: ['UPSTREAM-1'] },
    expected: 'WAIT_ACTIVE_WORK_DEPENDENCY',
  },
  {
    name: 'same target overlapping active delta waits',
    runs: [run({ status: 'RUNNING', runKey: 'RUN-X', objective: 'OBJ-X', target: 'TARGET-B' })],
    candidate: { ...baseCandidate, expectedDeltaOverlap: true },
    expected: 'WAIT_ACTIVE_WORK_TARGET_CONFLICT',
  },
  {
    name: 'same target explicit conflict waits',
    runs: [run({ status: 'RUNNING', runKey: 'RUN-X', objective: 'OBJ-X', target: 'TARGET-B' })],
    candidate: { ...baseCandidate, targetConflict: true },
    expected: 'WAIT_ACTIVE_WORK_TARGET_CONFLICT',
  },
  {
    name: 'independent active run does not block',
    runs: [run({ status: 'RUNNING', runKey: 'RUN-X', objective: 'OBJ-X', target: 'TARGET-X' })],
    candidate: baseCandidate,
    expected: 'PROCEED',
  },
  {
    name: 'same objective verified success becomes no-op',
    runs: [run({ status: 'SUCCEEDED', runKey: 'RUN-A', objective: 'OBJ-B', target: 'TARGET-B', verified: true })],
    candidate: baseCandidate,
    expected: 'NO_OP_ACTIVE_WORK_COMPLETED_SAME_OBJECTIVE',
  },
  {
    name: 'identical RUN_KEY verified success becomes no-op',
    runs: [run({ status: 'SUCCEEDED', runKey: 'RUN-B', objective: 'OLD-OBJ', target: 'TARGET-B', verified: true })],
    candidate: baseCandidate,
    expected: 'NO_OP_ACTIVE_WORK_COMPLETED_SAME_OBJECTIVE',
  },
  {
    name: 'provider success without same-target verification fails closed',
    runs: [run({ status: 'SUCCEEDED', runKey: 'RUN-B', objective: 'OBJ-B', target: 'TARGET-B', verified: false })],
    candidate: baseCandidate,
    expected: 'BLOCKED_ACTIVE_WORK_VISIBILITY_UNKNOWN',
  },
  {
    name: 'stale RUNNING signal fails closed',
    runs: [run({ status: 'RUNNING', runKey: 'RUN-B', objective: 'OBJ-B', target: 'TARGET-B', updated: '2026-09-17T02:00:00+08:00' })],
    candidate: baseCandidate,
    expected: 'BLOCKED_ACTIVE_WORK_VISIBILITY_UNKNOWN',
  },
  {
    name: 'failed same objective with unchanged blocker waits for trigger',
    runs: [run({ status: 'FAILED', runKey: 'RUN-B', objective: 'OBJ-B', target: 'TARGET-B', blockerFingerprint: 'BLOCKER-1' })],
    candidate: { ...baseCandidate, blockerFingerprint: 'BLOCKER-1' },
    expected: 'WAIT_TRIGGER_UNCHANGED_BLOCKER',
  },
  {
    name: 'failed same objective can proceed after material blocker change',
    runs: [run({ status: 'FAILED', runKey: 'RUN-B', objective: 'OBJ-B', target: 'TARGET-B', blockerFingerprint: 'BLOCKER-1' })],
    candidate: { ...baseCandidate, blockerFingerprint: 'BLOCKER-2' },
    expected: 'PROCEED',
  },
  {
    name: 'failed run after mutation becomes no-op when same-target readback proves desired state',
    runs: [run({ status: 'FAILED', runKey: 'RUN-B', objective: 'OBJ-B', target: 'TARGET-B', blockerFingerprint: 'INTERRUPTED' })],
    candidate: { ...baseCandidate, targetReadbackVerifiedSatisfied: true },
    expected: 'NO_OP_ACTIVE_WORK_COMPLETED_SAME_OBJECTIVE',
  },
];

let passed = 0;
for (const c of cases) {
  const result = buildPreflightFromActivepieces({
    runs: c.runs,
    candidate: c.candidate,
    preflight: basePreflight,
    options,
  });
  assert.equal(result.decision, c.expected, c.name);
  passed += 1;
}

// Multiple-provider-run resolution must prefer duplicate-risk evidence over unrelated runs.
{
  const selected = resolveActivepiecesSignal([
    run({ id: 'independent-newer', status: 'RUNNING', runKey: 'RUN-X', objective: 'OBJ-X', target: 'TARGET-X' }),
    run({ id: 'same-objective', status: 'RUNNING', runKey: 'RUN-A', objective: 'OBJ-B', target: 'TARGET-Y' }),
  ], baseCandidate, options);
  assert.equal(selected.providerRunId, 'same-objective');
  assert.equal(selected.relation, 'SAME_OBJECTIVE');
}

// Synthetic event -> admission -> mock gateway -> same-target readback -> verify -> identical RUN_KEY retry.
{
  const target = { value: 'v1' };
  const effects = new Set();
  let gatewayMutations = 0;
  const request = {
    runKey: 'RUN-IDEMPOTENT-1',
    objective: 'OBJ-IDEMPOTENT',
    target: 'TARGET-IDEMPOTENT',
  };

  const first = buildPreflightFromActivepieces({
    runs: [],
    candidate: request,
    preflight: { ...basePreflight, currentFingerprint: target.value, desiredFingerprint: 'v2' },
    options,
  });
  assert.equal(first.decision, 'PROCEED');

  if (!effects.has(request.runKey)) {
    target.value = 'v2';
    effects.add(request.runKey);
    gatewayMutations += 1;
  }
  assert.equal(target.value, 'v2');

  const completedRun = run({
    id: 'provider-completed',
    status: 'SUCCEEDED',
    runKey: request.runKey,
    objective: request.objective,
    target: request.target,
    verified: true,
  });
  const retry = buildPreflightFromActivepieces({
    runs: [completedRun],
    candidate: request,
    preflight: { ...basePreflight, currentFingerprint: target.value, desiredFingerprint: 'v2' },
    options,
  });
  assert.equal(retry.decision, 'NO_OP_ACTIVE_WORK_COMPLETED_SAME_OBJECTIVE');
  assert.equal(gatewayMutations, 1);
}

// Worker interruption after side effect but before provider success: readback absorbs retry.
{
  const target = { value: 'v1' };
  let gatewayMutations = 0;
  const request = {
    runKey: 'RUN-INTERRUPT-1',
    objective: 'OBJ-INTERRUPT',
    target: 'TARGET-INTERRUPT',
  };

  target.value = 'v2';
  gatewayMutations += 1;
  const interrupted = run({
    id: 'provider-interrupted',
    status: 'FAILED',
    runKey: request.runKey,
    objective: request.objective,
    target: request.target,
    blockerFingerprint: 'WORKER_INTERRUPTED',
  });

  const retry = buildPreflightFromActivepieces({
    runs: [interrupted],
    candidate: { ...request, targetReadbackVerifiedSatisfied: target.value === 'v2' },
    preflight: { ...basePreflight, currentFingerprint: target.value, desiredFingerprint: 'v2' },
    options,
  });
  assert.equal(retry.decision, 'NO_OP_ACTIVE_WORK_COMPLETED_SAME_OBJECTIVE');
  assert.equal(gatewayMutations, 1);
}

// Direct normalization preserves provider identity and tags without granting authority.
{
  const signal = normalizeActivepiecesRun(
    run({ status: 'RUNNING', runKey: 'R-CHECK', objective: 'O-CHECK', target: 'T-CHECK', lane: 'GITHUB_ACTIONS_PUBLIC' }),
    { runKey: 'R-CHECK', objective: 'O-CHECK', target: 'T-CHECK' },
    options,
  );
  assert.equal(signal.source, 'ACTIVEPIECES_FLOW_RUN');
  assert.equal(signal.projectId, PROJECT);
  assert.equal(signal.flowId, FLOW);
  assert.equal(signal.lane, 'GITHUB_ACTIONS_PUBLIC');
  assert.equal(signal.status, 'ACTIVE');
  assert.equal(signal.relation, 'SAME_OBJECTIVE');
}

console.log(JSON.stringify({
  event: 'hao_activepieces_active_work_contract',
  version: ACTIVEPIECES_SIGNAL_VERSION,
  result: 'PASS',
  adversarialCases: passed,
  providerRunResolution: 'PASS',
  identicalRunKeyRetry: 'NO_OP_AFTER_VERIFIED_READBACK',
  workerInterruptionRetry: 'NO_OP_WHEN_TARGET_READBACK_ALREADY_SATISFIED',
  providerRole: ACTIVEPIECES_ACTIVE_WORK_POLICY.providerRole,
  authority: ACTIVEPIECES_ACTIVE_WORK_POLICY.authority,
  noGlobalTaskDatabase: ACTIVEPIECES_ACTIVE_WORK_POLICY.noGlobalTaskDatabase,
}, null, 2));
