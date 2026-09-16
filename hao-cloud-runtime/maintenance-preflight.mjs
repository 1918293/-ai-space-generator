export const PREFLIGHT_VERSION = '0.2.0-exp';

export const PREFLIGHT_DECISIONS = Object.freeze({
  PROCEED: 'PROCEED',
  NO_OP_ALREADY_SATISFIED: 'NO_OP_ALREADY_SATISFIED',
  NO_OP_NO_MATERIAL_DELTA: 'NO_OP_NO_MATERIAL_DELTA',
  NO_OP_ACTIVE_WORK_COMPLETED_SAME_OBJECTIVE: 'NO_OP_ACTIVE_WORK_COMPLETED_SAME_OBJECTIVE',
  WAIT_TRIGGER_UNCHANGED_BLOCKER: 'WAIT_TRIGGER_UNCHANGED_BLOCKER',
  WAIT_ACTIVE_WORK_SAME_OBJECTIVE: 'WAIT_ACTIVE_WORK_SAME_OBJECTIVE',
  WAIT_ACTIVE_WORK_DEPENDENCY: 'WAIT_ACTIVE_WORK_DEPENDENCY',
  WAIT_ACTIVE_WORK_TARGET_CONFLICT: 'WAIT_ACTIVE_WORK_TARGET_CONFLICT',
  BLOCKED_FRESH_STATE_REQUIRED: 'BLOCKED_FRESH_STATE_REQUIRED',
  BLOCKED_CURRENT_RESOLUTION_REQUIRED: 'BLOCKED_CURRENT_RESOLUTION_REQUIRED',
  BLOCKED_TARGET_IDENTITY_REQUIRED: 'BLOCKED_TARGET_IDENTITY_REQUIRED',
  BLOCKED_ACTIVE_WORK_CHECK_REQUIRED: 'BLOCKED_ACTIVE_WORK_CHECK_REQUIRED',
  BLOCKED_ACTIVE_WORK_VISIBILITY_UNKNOWN: 'BLOCKED_ACTIVE_WORK_VISIBILITY_UNKNOWN',
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

export const ACTIVE_WORK_RELATIONS = Object.freeze([
  'NONE',
  'SAME_OBJECTIVE',
  'SAME_TARGET_DIFFERENT_OBJECTIVE',
  'UPSTREAM_DEPENDENCY',
  'INDEPENDENT',
  'UNKNOWN',
]);

export const ACTIVE_WORK_STATUSES = Object.freeze([
  'NONE',
  'ACTIVE',
  'COMPLETED',
  'BLOCKED',
  'UNKNOWN',
]);

function normalizeFingerprint(value) {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed.length ? trimmed : null;
}

function normalizeActiveWork(input = {}) {
  const aw = input.activeWork && typeof input.activeWork === 'object' ? input.activeWork : {};
  const status = ACTIVE_WORK_STATUSES.includes(aw.status) ? aw.status : 'UNKNOWN';
  const relation = ACTIVE_WORK_RELATIONS.includes(aw.relation) ? aw.relation : 'UNKNOWN';
  return {
    checked: aw.checked === true,
    status,
    relation,
    targetConflict: aw.targetConflict === true,
    expectedDeltaOverlap: aw.expectedDeltaOverlap === true,
    outcomeVerified: aw.outcomeVerified === true,
    source: typeof aw.source === 'string' && aw.source.trim() ? aw.source.trim() : null,
    runKey: typeof aw.runKey === 'string' && aw.runKey.trim() ? aw.runKey.trim() : null,
  };
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
    waitTrigger: decision.startsWith('WAIT_'),
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
  const activeWorkRequired = consequential || input.activeWorkRequired === true;
  const activeWork = normalizeActiveWork(input);

  if (!freshStateRead) {
    return result(input, PREFLIGHT_DECISIONS.BLOCKED_FRESH_STATE_REQUIRED,
      'Fresh provider/target state must be read before deciding whether maintenance is necessary.');
  }

  if (consequential && input.currentStateResolved !== true) {
    return result(input, PREFLIGHT_DECISIONS.BLOCKED_CURRENT_RESOLUTION_REQUIRED,
      'Fresh raw or historical state is not enough; consequential work requires resolved Current semantics.');
  }

  if (consequential && !targetIdentityVerified) {
    return result(input, PREFLIGHT_DECISIONS.BLOCKED_TARGET_IDENTITY_REQUIRED,
      'Consequential work requires verified target identity before execution.');
  }

  if (activeWorkRequired && !activeWork.checked) {
    return result(input, PREFLIGHT_DECISIONS.BLOCKED_ACTIVE_WORK_CHECK_REQUIRED,
      'Consequential work must check available active-work signals before material-delta evaluation.', { activeWork });
  }

  if (activeWorkRequired && (activeWork.status === 'UNKNOWN' || activeWork.relation === 'UNKNOWN')) {
    return result(input, PREFLIGHT_DECISIONS.BLOCKED_ACTIVE_WORK_VISIBILITY_UNKNOWN,
      'Active-work visibility is unresolved for consequential work; do not start potentially duplicate work.', { activeWork });
  }

  if (activeWork.status === 'COMPLETED' && activeWork.relation === 'SAME_OBJECTIVE' && activeWork.outcomeVerified) {
    return result(input, PREFLIGHT_DECISIONS.NO_OP_ACTIVE_WORK_COMPLETED_SAME_OBJECTIVE,
      'Another verified work item already completed the same objective.', { activeWork });
  }

  if (activeWork.status === 'ACTIVE' && activeWork.relation === 'SAME_OBJECTIVE') {
    return result(input, PREFLIGHT_DECISIONS.WAIT_ACTIVE_WORK_SAME_OBJECTIVE,
      'Another active work item is already pursuing the same objective; wait/join instead of duplicating execution.', { activeWork });
  }

  if (activeWork.status === 'ACTIVE' && activeWork.relation === 'UPSTREAM_DEPENDENCY') {
    return result(input, PREFLIGHT_DECISIONS.WAIT_ACTIVE_WORK_DEPENDENCY,
      'An active upstream dependency can materially change this task; wait for its readback before execution.', { activeWork });
  }

  if (activeWork.status === 'ACTIVE' && activeWork.relation === 'SAME_TARGET_DIFFERENT_OBJECTIVE' &&
      (activeWork.targetConflict || activeWork.expectedDeltaOverlap)) {
    return result(input, PREFLIGHT_DECISIONS.WAIT_ACTIVE_WORK_TARGET_CONFLICT,
      'Another active work item is changing the same target with overlapping or conflicting delta.', { activeWork });
  }

  if (input.blockerKnown === true && input.blockerChanged !== true) {
    return result(input, PREFLIGHT_DECISIONS.WAIT_TRIGGER_UNCHANGED_BLOCKER,
      'A known blocker is still unchanged; repeating the same action would be non-informative.', { activeWork });
  }

  if (input.objectiveSatisfied === true) {
    return result(input, PREFLIGHT_DECISIONS.NO_OP_ALREADY_SATISFIED,
      'The requested objective is already satisfied by fresh resolved Current state.', { activeWork });
  }

  if (currentFingerprint && desiredFingerprint && currentFingerprint === desiredFingerprint) {
    return result(input, PREFLIGHT_DECISIONS.NO_OP_NO_MATERIAL_DELTA,
      'Fresh resolved Current and desired fingerprints are identical; there is no material delta.', { activeWork });
  }

  if (consequential && input.necessityEstablished !== true) {
    return result(input, PREFLIGHT_DECISIONS.BLOCKED_NECESSITY_UNPROVEN,
      'Consequential work is blocked until a material need for the action is established.', { activeWork });
  }

  return result(input, PREFLIGHT_DECISIONS.PROCEED,
    'Fresh state, resolved Current, target identity, active-work awareness, material delta, and maintenance necessity are sufficient to proceed.',
    {
      activeWork,
      evidence: {
        freshStateRead: true,
        currentStateResolved: consequential ? true : null,
        targetIdentityVerified: consequential ? true : null,
        activeWorkChecked: activeWorkRequired ? true : null,
        activeWorkRelation: activeWorkRequired ? activeWork.relation : null,
        materialDelta: currentFingerprint && desiredFingerprint ? currentFingerprint !== desiredFingerprint : 'NOT_FINGERPRINT_COMPARABLE',
        necessityEstablished: consequential ? true : null,
      }
    }
  );
}

export const MAINTENANCE_PREFLIGHT_POLICY = Object.freeze({
  status: 'EXP_NON_AUTHORITY',
  purpose: 'Prevent stale-state action, duplicate active work, unnecessary retries, writes, deploys, transforms, and maintenance before downstream execution.',
  ruleOrder: [
    'FRESH_STATE',
    'CURRENT_RESOLUTION',
    'TARGET_IDENTITY',
    'ACTIVE_WORK_AWARENESS',
    'UNCHANGED_BLOCKER',
    'OBJECTIVE_ALREADY_SATISFIED',
    'MATERIAL_DELTA',
    'MAINTENANCE_NECESSITY',
    'PROCEED',
  ],
  activeWorkRelations: ACTIVE_WORK_RELATIONS,
  noOpIsValidOutcome: true,
  waitIsValidOutcome: true,
  formalMutationBoundary: 'SINGLE_WRITE_GATEWAY',
  supportedProviders: PREFLIGHT_PROVIDERS,
});
