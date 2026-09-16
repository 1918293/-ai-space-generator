export const STABILITY_VERSION = '0.1.0-exp';

export const STABILITY_DECISIONS = Object.freeze({
  STABLE_CONFIRMED: 'STABLE_CONFIRMED',
  RECONCILE_NEW_EPOCH: 'RECONCILE_NEW_EPOCH',
  BLOCKED_IMMEDIATE_READBACK_REQUIRED: 'BLOCKED_IMMEDIATE_READBACK_REQUIRED',
  BLOCKED_RELEASE_EVIDENCE_REQUIRED: 'BLOCKED_RELEASE_EVIDENCE_REQUIRED',
  BLOCKED_STABILITY_READBACK_REQUIRED: 'BLOCKED_STABILITY_READBACK_REQUIRED',
  INVALID_IMMEDIATE_READBACK_MISMATCH: 'INVALID_IMMEDIATE_READBACK_MISMATCH',
  INVALID_POST_RELEASE_CHANGE_SAME_EPOCH: 'INVALID_POST_RELEASE_CHANGE_SAME_EPOCH',
  INVALID_EPOCH_REGRESSION: 'INVALID_EPOCH_REGRESSION',
});

function present(value) {
  return typeof value === 'string' && value.trim().length > 0;
}

export function evaluateFormalWriteStability(input = {}) {
  const expectedFingerprint = input.expectedFingerprint;
  const expectedRunKey = input.expectedRunKey;
  const expectedLeaseEpoch = Number(input.expectedLeaseEpoch);
  const immediate = input.immediateReadback || {};
  const release = input.releaseEvidence || {};
  const stability = input.stabilityReadback || {};

  if (immediate.observed !== true) {
    return { version: STABILITY_VERSION, decision: STABILITY_DECISIONS.BLOCKED_IMMEDIATE_READBACK_REQUIRED, stable: false };
  }
  if (!present(expectedFingerprint) || immediate.targetFingerprint !== expectedFingerprint || immediate.runKey !== expectedRunKey || Number(immediate.leaseEpoch) !== expectedLeaseEpoch) {
    return { version: STABILITY_VERSION, decision: STABILITY_DECISIONS.INVALID_IMMEDIATE_READBACK_MISMATCH, stable: false };
  }
  if (release.observed !== true || release.state !== 'FREE' || Number(release.leaseEpoch) !== expectedLeaseEpoch) {
    return { version: STABILITY_VERSION, decision: STABILITY_DECISIONS.BLOCKED_RELEASE_EVIDENCE_REQUIRED, stable: false };
  }
  if (stability.observed !== true) {
    return { version: STABILITY_VERSION, decision: STABILITY_DECISIONS.BLOCKED_STABILITY_READBACK_REQUIRED, stable: false };
  }

  const laterEpoch = Number(stability.leaseEpoch);
  if (!Number.isFinite(laterEpoch) || laterEpoch < expectedLeaseEpoch) {
    return { version: STABILITY_VERSION, decision: STABILITY_DECISIONS.INVALID_EPOCH_REGRESSION, stable: false };
  }
  if (laterEpoch > expectedLeaseEpoch) {
    return {
      version: STABILITY_VERSION,
      decision: STABILITY_DECISIONS.RECONCILE_NEW_EPOCH,
      stable: false,
      reason: 'A newer formal writer exists; reconcile its verified result instead of treating the earlier fingerprint as final.',
    };
  }
  if (stability.targetFingerprint !== expectedFingerprint) {
    return {
      version: STABILITY_VERSION,
      decision: STABILITY_DECISIONS.INVALID_POST_RELEASE_CHANGE_SAME_EPOCH,
      stable: false,
      reason: 'Target changed after release without a newer lease epoch. Completion evidence is invalid until reconciled.',
    };
  }

  return {
    version: STABILITY_VERSION,
    decision: STABILITY_DECISIONS.STABLE_CONFIRMED,
    stable: true,
    reason: 'Immediate identity readback, release evidence, and post-release stability readback agree at the same lease epoch.',
  };
}

export const FORMAL_WRITE_STABILITY_POLICY = Object.freeze({
  status: 'EXP_NON_AUTHORITY',
  naturalUseRequired: false,
  requiredEvidence: [
    'IMMEDIATE_IDENTITY_READBACK',
    'OWNER_SAFE_RELEASE_EVIDENCE',
    'POST_RELEASE_STABILITY_READBACK',
  ],
  sameEpochTargetChange: 'INVALIDATE_COMPLETION_AND_RECONCILE',
  newerEpochTargetChange: 'RECONCILE_NEWER_WRITER',
});
