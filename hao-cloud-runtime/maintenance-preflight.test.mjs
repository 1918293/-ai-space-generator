import assert from 'node:assert/strict';
import {
  evaluateMaintenancePreflight,
  PREFLIGHT_DECISIONS,
  MAINTENANCE_PREFLIGHT_POLICY,
  PREFLIGHT_VERSION,
} from './maintenance-preflight.mjs';

const cases = [
  {
    name: 'GitHub identical file content becomes no-op',
    input: { provider: 'GITHUB', action: 'UPDATE_FILE', freshStateRead: true, targetIdentityVerified: true, mutating: true, necessityEstablished: true, currentFingerprint: 'sha:abc', desiredFingerprint: 'sha:abc' },
    expected: PREFLIGHT_DECISIONS.NO_OP_NO_MATERIAL_DELTA,
  },
  {
    name: 'GitHub changed file can proceed',
    input: { provider: 'GITHUB', action: 'UPDATE_FILE', freshStateRead: true, targetIdentityVerified: true, mutating: true, necessityEstablished: true, currentFingerprint: 'sha:abc', desiredFingerprint: 'sha:def' },
    expected: PREFLIGHT_DECISIONS.PROCEED,
  },
  {
    name: 'Render already at desired commit becomes no-op',
    input: { provider: 'RENDER', action: 'DEPLOY', freshStateRead: true, targetIdentityVerified: true, mutating: true, necessityEstablished: true, currentFingerprint: 'commit:123', desiredFingerprint: 'commit:123' },
    expected: PREFLIGHT_DECISIONS.NO_OP_NO_MATERIAL_DELTA,
  },
  {
    name: 'Render failed residue with no active incident needs no maintenance',
    input: { provider: 'RENDER', action: 'CLEANUP', freshStateRead: true, targetIdentityVerified: true, mutating: true, objectiveSatisfied: true, necessityEstablished: true },
    expected: PREFLIGHT_DECISIONS.NO_OP_ALREADY_SATISFIED,
  },
  {
    name: 'Drive formal mutation requires necessity evidence',
    input: { provider: 'DRIVE', action: 'FORMAL_WRITE', freshStateRead: true, targetIdentityVerified: true, formalMutation: true, currentFingerprint: 'v1', desiredFingerprint: 'v2' },
    expected: PREFLIGHT_DECISIONS.BLOCKED_NECESSITY_UNPROVEN,
  },
  {
    name: 'Drive formal mutation with delta and necessity can proceed through gateway',
    input: { provider: 'DRIVE', action: 'FORMAL_WRITE', freshStateRead: true, targetIdentityVerified: true, formalMutation: true, necessityEstablished: true, currentFingerprint: 'v1', desiredFingerprint: 'v2' },
    expected: PREFLIGHT_DECISIONS.PROCEED,
    boundary: 'SINGLE_WRITE_GATEWAY',
  },
  {
    name: 'Apps Script unchanged deployment blocker waits for trigger',
    input: { provider: 'APPS_SCRIPT', action: 'DEPLOY', freshStateRead: true, targetIdentityVerified: true, mutating: true, blockerKnown: true, blockerChanged: false, necessityEstablished: true },
    expected: PREFLIGHT_DECISIONS.WAIT_TRIGGER_UNCHANGED_BLOCKER,
  },
  {
    name: 'Apps Script changed blocker can proceed if necessity is established',
    input: { provider: 'APPS_SCRIPT', action: 'DEPLOY', freshStateRead: true, targetIdentityVerified: true, mutating: true, blockerKnown: true, blockerChanged: true, necessityEstablished: true },
    expected: PREFLIGHT_DECISIONS.PROCEED,
  },
  {
    name: 'Image exact pipeline with already verified same recipe becomes no-op',
    input: { provider: 'IMAGE_PIPELINE', action: 'EXACT_EDIT', freshStateRead: true, targetIdentityVerified: true, consequential: true, objectiveSatisfied: true, necessityEstablished: true, currentFingerprint: 'source+mask+recipe:v1', desiredFingerprint: 'source+mask+recipe:v1' },
    expected: PREFLIGHT_DECISIONS.NO_OP_ALREADY_SATISFIED,
  },
  {
    name: 'Image pipeline changed mask can proceed',
    input: { provider: 'IMAGE_PIPELINE', action: 'EXACT_EDIT', freshStateRead: true, targetIdentityVerified: true, consequential: true, necessityEstablished: true, currentFingerprint: 'source+maskA+recipe:v1', desiredFingerprint: 'source+maskB+recipe:v1' },
    expected: PREFLIGHT_DECISIONS.PROCEED,
  },
  {
    name: 'Any consequential action without fresh state blocks',
    input: { provider: 'GENERIC', action: 'MUTATE', freshStateRead: false, targetIdentityVerified: true, mutating: true, necessityEstablished: true },
    expected: PREFLIGHT_DECISIONS.BLOCKED_FRESH_STATE_REQUIRED,
  },
  {
    name: 'Any consequential action with unresolved target identity blocks',
    input: { provider: 'GENERIC', action: 'MUTATE', freshStateRead: true, targetIdentityVerified: false, mutating: true, necessityEstablished: true },
    expected: PREFLIGHT_DECISIONS.BLOCKED_TARGET_IDENTITY_REQUIRED,
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
assert.equal(MAINTENANCE_PREFLIGHT_POLICY.formalMutationBoundary, 'SINGLE_WRITE_GATEWAY');
assert.deepEqual(MAINTENANCE_PREFLIGHT_POLICY.supportedProviders, ['GITHUB','RENDER','DRIVE','APPS_SCRIPT','IMAGE_PIPELINE','GENERIC']);

console.log(JSON.stringify({
  event: 'hao_maintenance_preflight_contract',
  contractVersion: PREFLIGHT_VERSION,
  passed: results.length,
  total: cases.length,
  result: 'PASS',
  noOpIsValidOutcome: true,
  providers: MAINTENANCE_PREFLIGHT_POLICY.supportedProviders,
  results,
}, null, 2));
