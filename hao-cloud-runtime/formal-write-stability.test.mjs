import assert from 'node:assert/strict';
import {
  evaluateFormalWriteStability,
  STABILITY_DECISIONS,
  FORMAL_WRITE_STABILITY_POLICY,
} from './formal-write-stability.mjs';

const base = {
  expectedFingerprint: 'target:v2',
  expectedRunKey: 'RUN-1',
  expectedLeaseEpoch: 359,
  immediateReadback: {
    observed: true,
    targetFingerprint: 'target:v2',
    runKey: 'RUN-1',
    leaseEpoch: 359,
  },
  releaseEvidence: {
    observed: true,
    state: 'FREE',
    leaseEpoch: 359,
  },
  stabilityReadback: {
    observed: true,
    targetFingerprint: 'target:v2',
    leaseEpoch: 359,
  },
};

const cases = [
  ['stable same-epoch closeout passes', {}, STABILITY_DECISIONS.STABLE_CONFIRMED, true],
  ['missing immediate readback blocks', { immediateReadback: { observed: false } }, STABILITY_DECISIONS.BLOCKED_IMMEDIATE_READBACK_REQUIRED, false],
  ['wrong immediate fingerprint invalidates', { immediateReadback: { ...base.immediateReadback, targetFingerprint: 'target:v1' } }, STABILITY_DECISIONS.INVALID_IMMEDIATE_READBACK_MISMATCH, false],
  ['missing release evidence blocks', { releaseEvidence: { observed: false } }, STABILITY_DECISIONS.BLOCKED_RELEASE_EVIDENCE_REQUIRED, false],
  ['missing stability readback blocks', { stabilityReadback: { observed: false } }, STABILITY_DECISIONS.BLOCKED_STABILITY_READBACK_REQUIRED, false],
  ['same-epoch late write is detected', { stabilityReadback: { observed: true, targetFingerprint: 'target:v3-late', leaseEpoch: 359 } }, STABILITY_DECISIONS.INVALID_POST_RELEASE_CHANGE_SAME_EPOCH, false],
  ['newer epoch change reconciles rather than falsely passing', { stabilityReadback: { observed: true, targetFingerprint: 'target:v3', leaseEpoch: 360 } }, STABILITY_DECISIONS.RECONCILE_NEW_EPOCH, false],
  ['epoch regression invalidates', { stabilityReadback: { observed: true, targetFingerprint: 'target:v2', leaseEpoch: 358 } }, STABILITY_DECISIONS.INVALID_EPOCH_REGRESSION, false],
];

for (const [name, patch, expectedDecision, expectedStable] of cases) {
  const actual = evaluateFormalWriteStability({ ...base, ...patch });
  assert.equal(actual.decision, expectedDecision, name);
  assert.equal(actual.stable, expectedStable, name);
}

// Controlled concurrency/release replay.
const replay = [];
let actual = evaluateFormalWriteStability(base);
assert.equal(actual.decision, STABILITY_DECISIONS.STABLE_CONFIRMED);
replay.push('same_epoch_same_fingerprint=>STABLE');

actual = evaluateFormalWriteStability({
  ...base,
  stabilityReadback: { observed: true, targetFingerprint: 'target:late-write', leaseEpoch: 359 },
});
assert.equal(actual.decision, STABILITY_DECISIONS.INVALID_POST_RELEASE_CHANGE_SAME_EPOCH);
replay.push('same_epoch_changed_fingerprint=>INVALIDATE_COMPLETION');

actual = evaluateFormalWriteStability({
  ...base,
  stabilityReadback: { observed: true, targetFingerprint: 'target:new-writer', leaseEpoch: 360 },
});
assert.equal(actual.decision, STABILITY_DECISIONS.RECONCILE_NEW_EPOCH);
replay.push('new_epoch_changed_fingerprint=>RECONCILE_NEW_WRITER');

assert.equal(FORMAL_WRITE_STABILITY_POLICY.naturalUseRequired, false);
assert.equal(FORMAL_WRITE_STABILITY_POLICY.sameEpochTargetChange, 'INVALIDATE_COMPLETION_AND_RECONCILE');

console.log(JSON.stringify({
  event: 'hao_formal_write_post_release_stability_validation',
  result: 'PASS',
  cases: cases.length,
  replay,
  naturalUseRequired: false,
}, null, 2));
