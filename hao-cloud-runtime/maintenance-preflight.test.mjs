import assert from 'node:assert/strict';
import {
  evaluateMaintenancePreflight,
  PREFLIGHT_DECISIONS,
  MAINTENANCE_PREFLIGHT_POLICY,
  PREFLIGHT_VERSION,
} from './maintenance-preflight.mjs';

const none = { checked: true, status: 'NONE', relation: 'NONE', source: 'PROVIDER_CURRENT' };
const base = {
  freshStateRead: true,
  currentStateResolved: true,
  targetIdentityVerified: true,
  necessityEstablished: true,
  activeWork: none,
};

const cases = [
  {
    name: 'GitHub identical file content becomes no-op',
    input: { ...base, provider: 'GITHUB', action: 'UPDATE_FILE', mutating: true, currentFingerprint: 'sha:abc', desiredFingerprint: 'sha:abc' },
    expected: PREFLIGHT_DECISIONS.NO_OP_NO_MATERIAL_DELTA,
  },
  {
    name: 'GitHub changed file can proceed',
    input: { ...base, provider: 'GITHUB', action: 'UPDATE_FILE', mutating: true, currentFingerprint: 'sha:abc', desiredFingerprint: 'sha:def' },
    expected: PREFLIGHT_DECISIONS.PROCEED,
  },
  {
    name: 'Render already at desired commit becomes no-op',
    input: { ...base, provider: 'RENDER', action: 'DEPLOY', mutating: true, currentFingerprint: 'commit:123', desiredFingerprint: 'commit:123' },
    expected: PREFLIGHT_DECISIONS.NO_OP_NO_MATERIAL_DELTA,
  },
  {
    name: 'Render failed residue with no active incident needs no maintenance',
    input: { ...base, provider: 'RENDER', action: 'CLEANUP', mutating: true, objectiveSatisfied: true },
    expected: PREFLIGHT_DECISIONS.NO_OP_ALREADY_SATISFIED,
  },
  {
    name: 'Drive formal mutation requires necessity evidence',
    input: { ...base, provider: 'DRIVE', action: 'FORMAL_WRITE', formalMutation: true, necessityEstablished: false, currentFingerprint: 'v1', desiredFingerprint: 'v2' },
    expected: PREFLIGHT_DECISIONS.BLOCKED_NECESSITY_UNPROVEN,
  },
  {
    name: 'Drive formal mutation with delta and necessity can proceed through gateway',
    input: { ...base, provider: 'DRIVE', action: 'FORMAL_WRITE', formalMutation: true, currentFingerprint: 'v1', desiredFingerprint: 'v2' },
    expected: PREFLIGHT_DECISIONS.PROCEED,
    boundary: 'SINGLE_WRITE_GATEWAY',
  },
  {
    name: 'Apps Script unchanged deployment blocker waits for trigger',
    input: { ...base, provider: 'APPS_SCRIPT', action: 'DEPLOY', mutating: true, blockerKnown: true, blockerChanged: false },
    expected: PREFLIGHT_DECISIONS.WAIT_TRIGGER_UNCHANGED_BLOCKER,
  },
  {
    name: 'Apps Script changed blocker can proceed if necessity is established',
    input: { ...base, provider: 'APPS_SCRIPT', action: 'DEPLOY', mutating: true, blockerKnown: true, blockerChanged: true },
    expected: PREFLIGHT_DECISIONS.PROCEED,
  },
  {
    name: 'Image exact pipeline with already verified same recipe becomes no-op',
    input: { ...base, provider: 'IMAGE_PIPELINE', action: 'EXACT_EDIT', consequential: true, objectiveSatisfied: true, currentFingerprint: 'source+mask+recipe:v1', desiredFingerprint: 'source+mask+recipe:v1' },
    expected: PREFLIGHT_DECISIONS.NO_OP_ALREADY_SATISFIED,
  },
  {
    name: 'Image pipeline changed mask can proceed',
    input: { ...base, provider: 'IMAGE_PIPELINE', action: 'EXACT_EDIT', consequential: true, currentFingerprint: 'source+maskA+recipe:v1', desiredFingerprint: 'source+maskB+recipe:v1' },
    expected: PREFLIGHT_DECISIONS.PROCEED,
  },
  {
    name: 'Any consequential action without fresh state blocks',
    input: { ...base, provider: 'GENERIC', action: 'MUTATE', freshStateRead: false, mutating: true },
    expected: PREFLIGHT_DECISIONS.BLOCKED_FRESH_STATE_REQUIRED,
  },
  {
    name: 'Fresh raw historical state is not resolved Current',
    input: { ...base, provider: 'GENERIC', action: 'MUTATE', currentStateResolved: false, mutating: true },
    expected: PREFLIGHT_DECISIONS.BLOCKED_CURRENT_RESOLUTION_REQUIRED,
  },
  {
    name: 'Any consequential action with unresolved target identity blocks',
    input: { ...base, provider: 'GENERIC', action: 'MUTATE', targetIdentityVerified: false, mutating: true },
    expected: PREFLIGHT_DECISIONS.BLOCKED_TARGET_IDENTITY_REQUIRED,
  },
  {
    name: 'Consequential action without active-work check blocks before delta work',
    input: { ...base, provider: 'GENERIC', action: 'MUTATE', mutating: true, activeWork: { checked: false } },
    expected: PREFLIGHT_DECISIONS.BLOCKED_ACTIVE_WORK_CHECK_REQUIRED,
  },
  {
    name: 'Unknown active-work visibility blocks consequential action',
    input: { ...base, provider: 'GENERIC', action: 'MUTATE', mutating: true, activeWork: { checked: true, status: 'UNKNOWN', relation: 'UNKNOWN', source: 'UNRESOLVED' } },
    expected: PREFLIGHT_DECISIONS.BLOCKED_ACTIVE_WORK_VISIBILITY_UNKNOWN,
  },
  {
    name: 'Same objective already active becomes wait instead of duplicate work',
    input: { ...base, provider: 'DRIVE', action: 'FORMAL_WRITE', formalMutation: true, activeWork: { checked: true, status: 'ACTIVE', relation: 'SAME_OBJECTIVE', targetConflict: true, expectedDeltaOverlap: true, source: 'FORMAL_LEASE', runKey: 'other-run' } },
    expected: PREFLIGHT_DECISIONS.WAIT_ACTIVE_WORK_SAME_OBJECTIVE,
  },
  {
    name: 'Verified same objective completed by another work item becomes no-op',
    input: { ...base, provider: 'DRIVE', action: 'FORMAL_WRITE', formalMutation: true, activeWork: { checked: true, status: 'COMPLETED', relation: 'SAME_OBJECTIVE', outcomeVerified: true, source: 'FORMAL_READBACK', runKey: 'other-run' } },
    expected: PREFLIGHT_DECISIONS.NO_OP_ACTIVE_WORK_COMPLETED_SAME_OBJECTIVE,
  },
  {
    name: 'Active upstream dependency waits for readback',
    input: { ...base, provider: 'RENDER', action: 'DEPLOY', mutating: true, activeWork: { checked: true, status: 'ACTIVE', relation: 'UPSTREAM_DEPENDENCY', source: 'GITHUB_ACTIONS', runKey: 'build-run' } },
    expected: PREFLIGHT_DECISIONS.WAIT_ACTIVE_WORK_DEPENDENCY,
  },
  {
    name: 'Same target different objective with overlapping delta waits',
    input: { ...base, provider: 'GITHUB', action: 'UPDATE_FILE', mutating: true, activeWork: { checked: true, status: 'ACTIVE', relation: 'SAME_TARGET_DIFFERENT_OBJECTIVE', expectedDeltaOverlap: true, source: 'GITHUB', runKey: 'other-run' } },
    expected: PREFLIGHT_DECISIONS.WAIT_ACTIVE_WORK_TARGET_CONFLICT,
  },
  {
    name: 'Same target different objective without conflict can proceed',
    input: { ...base, provider: 'GITHUB', action: 'UPDATE_FILE', mutating: true, currentFingerprint: 'a', desiredFingerprint: 'b', activeWork: { checked: true, status: 'ACTIVE', relation: 'SAME_TARGET_DIFFERENT_OBJECTIVE', targetConflict: false, expectedDeltaOverlap: false, source: 'GITHUB', runKey: 'other-run' } },
    expected: PREFLIGHT_DECISIONS.PROCEED,
  },
  {
    name: 'Independent active work does not block',
    input: { ...base, provider: 'RENDER', action: 'DEPLOY', mutating: true, currentFingerprint: 'old', desiredFingerprint: 'new', activeWork: { checked: true, status: 'ACTIVE', relation: 'INDEPENDENT', source: 'RENDER', runKey: 'other-run' } },
    expected: PREFLIGHT_DECISIONS.PROCEED,
  },
];

const results = [];
for (const c of cases) {
  const actual = evaluateMaintenancePreflight(c.input);
  assert.equal(actual.contractVersion, PREFLIGHT_VERSION);
  assert.equal(actual.decision, c.expected, c.name);
  assert.equal(actual.shouldExecute, c.expected === PREFLIGHT_DECISIONS.PROCEED, c.name);
  if (c.boundary) assert.equal(actual.executionBoundary, c.boundary, c.name);
  results.push({ name: c.name, decision: actual.decision, pass: true });
}

assert.equal(MAINTENANCE_PREFLIGHT_POLICY.noOpIsValidOutcome, true);
assert.equal(MAINTENANCE_PREFLIGHT_POLICY.waitIsValidOutcome, true);
assert.equal(MAINTENANCE_PREFLIGHT_POLICY.formalMutationBoundary, 'SINGLE_WRITE_GATEWAY');
assert.equal(MAINTENANCE_PREFLIGHT_POLICY.ruleOrder[1], 'CURRENT_RESOLUTION');
assert.equal(MAINTENANCE_PREFLIGHT_POLICY.ruleOrder[3], 'ACTIVE_WORK_AWARENESS');
assert.deepEqual(MAINTENANCE_PREFLIGHT_POLICY.supportedProviders, ['GITHUB','RENDER','DRIVE','APPS_SCRIPT','IMAGE_PIPELINE','GENERIC']);

console.log(JSON.stringify({
  event: 'hao_maintenance_preflight_contract',
  contractVersion: PREFLIGHT_VERSION,
  passed: results.length,
  total: cases.length,
  result: 'PASS',
  noOpIsValidOutcome: true,
  waitIsValidOutcome: true,
  providers: MAINTENANCE_PREFLIGHT_POLICY.supportedProviders,
  results,
}, null, 2));
