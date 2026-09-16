export const PREFLIGHT_VERSION = '0.1.0-exp';

export const PREFLIGHT_DECISIONS = Object.freeze({
  PROCEED: 'PROCEED',
  NO_OP_ALREADY_SATISFIED: 'NO_OP_ALREADY_SATISFIED',
  NO_OP_NO_MATERIAL_DELTA: 'NO_OP_NO_MATERIAL_DELTA',
  WAIT_TRIGGER_UNCHANGED_BLOCKER: 'WAIT_TRIGGER_UNCHANGED_BLOCKER',
  BLOCKED_FRESH_STATE_REQUIRED: 'BLOCKED_FRESH_STATE_REQUIRED',
  BLOCKED_TARGET_IDENTITY_REQUIRED: 'BLOCKED_TARGET_IDENTITY_REQUIRED',
  BLOCKED_NECESSITY_UNPROVEN: 'BLOCKED_NECESSITY_UNPROVEN',
});

export const PREFLIGHT_PROVIDERS = Object.freeze([
  'GITHUB',
  'RENDER',
  'DRIVE',
  'APPS_SCRIPT',
  'IMAGE_PIPELINE',
  'GENERIC',
]);

function normalizeFingerprint(value) {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed.length ? trimmed : null;
}

function result(input, decision, reason, extra = {}) {
  const formalMutation = input.formalMutation === true;
  const shouldExecute = decision === PREFLIGHT_DECISIONS.PROCEED;
  return {
    contractVersion: PREFLIGHT_VERSION,
    provider: PREFLIGHT_PROVIDERS.includes(input.provider) ? input.provider : 'GENERIC',
    action: typeof input.action === 'string' && input.action.trim() ? input.action.trim() : 'MAINTENANCE',
    decision,
    shouldExecute,
    isNoOp: decision.startsWith('NO_OP_'),
    waitTrigger: decision === PREFLIGHT_DECISIONS.WAIT_TRIGGER_UNCHANGED_BLOCKER,
    reason,
    executionBoundary: formalMutation ? 'SINGLE_WRITE_GATEWAY' : 'NORMAL_EXECUTION_LANE',
    postconditions: shouldExecute
      ? ['EXECUTE_ONLY_REQUESTED_ACTION', 'READBACK_TARGET', 'VERIFY_OUTCOME']
      : ['NO_DOWNSTREAM_MUTATION', 'RETAIN_DECISION_EVIDENCE'],
    ...extra,
  };
}

export function evaluateMaintenancePreflight(input = {}) {
  const freshStateRead = input.freshStateRead === true;
  const mutating = input.mutating === true;
  const consequential = input.consequential === true || mutating || input.formalMutation === true;
  const targetIdentityVerified = input.targetIdentityVerified === true;
  const currentFingerprint = normalizeFingerprint(input.currentFingerprint);
  const desiredFingerprint = normalizeFingerprint(input.desiredFingerprint);

  if (!freshStateRead) {
    return result(
      input,
      PREFLIGHT_DECISIONS.BLOCKED_FRESH_STATE_REQUIRED,
      'Fresh provider/target state must be read before deciding whether maintenance is necessary.'
    );
  }

  if (consequential && !targetIdentityVerified) {
    return result(
      input,
      PREFLIGHT_DECISIONS.BLOCKED_TARGET_IDENTITY_REQUIRED,
      'Consequential work requires verified target identity before execution.'
    );
  }

  if (input.blockerKnown === true && input.blockerChanged !== true) {
    return result(
      input,
      PREFLIGHT_DECISIONS.WAIT_TRIGGER_UNCHANGED_BLOCKER,
      'A known blocker is still unchanged; repeating the same action would be non-informative.'
    );
  }

  if (input.objectiveSatisfied === true) {
    return result(
      input,
      PREFLIGHT_DECISIONS.NO_OP_ALREADY_SATISFIED,
      'The requested objective is already satisfied by fresh current state.'
    );
  }

  if (currentFingerprint && desiredFingerprint && currentFingerprint === desiredFingerprint) {
    return result(
      input,
      PREFLIGHT_DECISIONS.NO_OP_NO_MATERIAL_DELTA,
      'Fresh current and desired fingerprints are identical; there is no material delta.'
    );
  }

  if (consequential && input.necessityEstablished !== true) {
    return result(
      input,
      PREFLIGHT_DECISIONS.BLOCKED_NECESSITY_UNPROVEN,
      'Consequential work is blocked until a material need for the action is established.'
    );
  }

  return result(
    input,
    PREFLIGHT_DECISIONS.PROCEED,
    'Fresh state, target identity, material delta, and maintenance necessity are sufficient to proceed.',
    {
      evidence: {
        freshStateRead: true,
        targetIdentityVerified: consequential ? true : null,
        materialDelta: currentFingerprint && desiredFingerprint ? currentFingerprint !== desiredFingerprint : 'NOT_FINGERPRINT_COMPARABLE',
        necessityEstablished: consequential ? true : null,
      }
    }
  );
}

export const MAINTENANCE_PREFLIGHT_POLICY = Object.freeze({
  status: 'EXP_NON_AUTHORITY',
  purpose: 'Prevent unnecessary retries, writes, deploys, transforms, and maintenance before downstream execution.',
  ruleOrder: [
    'FRESH_STATE',
    'TARGET_IDENTITY',
    'UNCHANGED_BLOCKER',
    'OBJECTIVE_ALREADY_SATISFIED',
    'MATERIAL_DELTA',
    'MAINTENANCE_NECESSITY',
    'PROCEED',
  ],
  noOpIsValidOutcome: true,
  formalMutationBoundary: 'SINGLE_WRITE_GATEWAY',
  supportedProviders: PREFLIGHT_PROVIDERS,
});
