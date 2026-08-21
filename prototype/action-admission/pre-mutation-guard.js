'use strict';

const { OUTCOMES } = require('./admission');

const MUTATION_DECISIONS = Object.freeze({
  MUTATE_CANDIDATE: 'MUTATE_CANDIDATE',
  NO_OP: 'NO_OP',
  ABSTAIN: 'ABSTAIN',
});

const MUTATION_REASONS = Object.freeze({
  ADMISSION_REQUIRED: 'ADMISSION_REQUIRED',
  OPERATION_MISMATCH: 'OPERATION_MISMATCH',
  TARGET_MISMATCH: 'TARGET_MISMATCH',
  MUTATION_KIND_REQUIRED: 'MUTATION_KIND_REQUIRED',
  STRATEGY_REQUIRED: 'STRATEGY_REQUIRED',
  STRONG_PRECONDITION_REQUIRED: 'STRONG_PRECONDITION_REQUIRED',
  PRECONDITION_MISMATCH: 'PRECONDITION_MISMATCH',
  STALE_TARGET: 'STALE_TARGET',
  SCOPE_MISMATCH: 'SCOPE_MISMATCH',
  NO_DELTA: 'NO_DELTA',
  DELTA_EVIDENCE_REQUIRED: 'DELTA_EVIDENCE_REQUIRED',
  REQUEST_MISMATCH: 'REQUEST_MISMATCH',
  DEDUPE_REQUIRED: 'DEDUPE_REQUIRED',
  IDEMPOTENCY_MISMATCH: 'IDEMPOTENCY_MISMATCH',
  STALE_DUPLICATE_CHECK: 'STALE_DUPLICATE_CHECK',
  DUPLICATE_EXISTS: 'DUPLICATE_EXISTS',
  AMBIGUOUS_RETRY: 'AMBIGUOUS_RETRY',
  READBACK_REQUIRED: 'READBACK_REQUIRED',
  EXPECTED_OUTCOME_REQUIRED: 'EXPECTED_OUTCOME_REQUIRED',
});

const DURABLE_ADMISSIONS = new Set([
  OUTCOMES.PERSIST_CANDIDATE,
  OUTCOMES.ENFORCE_CANDIDATE,
]);

const SAFE_BEST_EFFORT_PRIOR_STATES = new Set(['NONE', 'CONFIRMED_FAILED']);
const BEST_EFFORT_RISKS = new Set(['LOW', 'MEDIUM']);

function decision(outcome, reason = null, details = null) {
  return Object.freeze({ outcome, reason, details });
}

function requireExpectedOutcome(value) {
  if (!value.expected_outcome) {
    return decision(
      MUTATION_DECISIONS.ABSTAIN,
      MUTATION_REASONS.EXPECTED_OUTCOME_REQUIRED,
      'expected observable outcome is required before mutation'
    );
  }
  return null;
}

function requireBalancedBestEffort(value) {
  if (!BEST_EFFORT_RISKS.has(value.mutation_risk) || value.critical_effect === true) {
    return decision(
      MUTATION_DECISIONS.ABSTAIN,
      MUTATION_REASONS.STRONG_PRECONDITION_REQUIRED,
      'high-risk or critical durable mutations require a strong server-side precondition/idempotency control'
    );
  }

  if (!SAFE_BEST_EFFORT_PRIOR_STATES.has(value.prior_attempt_state)) {
    return decision(
      MUTATION_DECISIONS.ABSTAIN,
      MUTATION_REASONS.AMBIGUOUS_RETRY,
      'best-effort mutation cannot auto-retry after an unknown prior outcome'
    );
  }

  if (
    !value.approved_request_fingerprint ||
    value.planned_request_fingerprint !== value.approved_request_fingerprint
  ) {
    return decision(
      MUTATION_DECISIONS.ABSTAIN,
      MUTATION_REASONS.REQUEST_MISMATCH,
      'best-effort mutation payload must match the admitted request fingerprint'
    );
  }

  if (value.post_write_readback_required !== true) {
    return decision(
      MUTATION_DECISIONS.ABSTAIN,
      MUTATION_REASONS.READBACK_REQUIRED,
      'best-effort mutation requires post-write readback verification'
    );
  }

  return null;
}

function classifyUpdate(value) {
  if (value.target_freshness !== 'CURRENT') {
    return decision(
      MUTATION_DECISIONS.ABSTAIN,
      MUTATION_REASONS.STALE_TARGET,
      'target must be freshly read before mutation'
    );
  }

  if (value.update_strategy === 'STRONG_PRECONDITION') {
    if (
      !value.approved_precondition_token ||
      value.planned_precondition_token !== value.approved_precondition_token
    ) {
      return decision(
        MUTATION_DECISIONS.ABSTAIN,
        MUTATION_REASONS.PRECONDITION_MISMATCH,
        'planned mutation must use the exact version/precondition token captured at admission'
      );
    }
  } else if (value.update_strategy === 'BEST_EFFORT') {
    const balancedGate = requireBalancedBestEffort(value);
    if (balancedGate) return balancedGate;
  } else {
    return decision(
      MUTATION_DECISIONS.ABSTAIN,
      MUTATION_REASONS.STRATEGY_REQUIRED,
      'update strategy must be STRONG_PRECONDITION or BEST_EFFORT'
    );
  }

  const currentHash = value.current_content_hash;
  const proposedHash = value.proposed_content_hash;

  if (currentHash && proposedHash && currentHash === proposedHash) {
    return decision(
      MUTATION_DECISIONS.NO_OP,
      MUTATION_REASONS.NO_DELTA,
      'proposed durable state is identical to current durable state'
    );
  }

  if (value.has_meaningful_delta !== true) {
    return decision(
      MUTATION_DECISIONS.NO_OP,
      MUTATION_REASONS.NO_DELTA,
      'no meaningful durable delta was established'
    );
  }

  if (!currentHash || !proposedHash) {
    return decision(
      MUTATION_DECISIONS.ABSTAIN,
      MUTATION_REASONS.DELTA_EVIDENCE_REQUIRED,
      'durable mutation requires comparable current and proposed state fingerprints'
    );
  }

  return requireExpectedOutcome(value) || decision(MUTATION_DECISIONS.MUTATE_CANDIDATE);
}

function classifyCreate(value) {
  if (
    !value.approved_request_fingerprint ||
    value.planned_request_fingerprint !== value.approved_request_fingerprint
  ) {
    return decision(
      MUTATION_DECISIONS.ABSTAIN,
      MUTATION_REASONS.REQUEST_MISMATCH,
      'planned create payload must match the admitted request fingerprint'
    );
  }

  if (value.create_dedupe_mode === 'SERVER_IDEMPOTENCY') {
    if (
      !value.approved_idempotency_key ||
      value.planned_idempotency_key !== value.approved_idempotency_key
    ) {
      return decision(
        MUTATION_DECISIONS.ABSTAIN,
        MUTATION_REASONS.IDEMPOTENCY_MISMATCH,
        'server-idempotent create must reuse the exact admitted idempotency key'
      );
    }
    return requireExpectedOutcome(value) || decision(MUTATION_DECISIONS.MUTATE_CANDIDATE);
  }

  if (value.create_dedupe_mode === 'FRESH_DUPLICATE_CHECK') {
    const balancedGate = requireBalancedBestEffort(value);
    if (balancedGate) return balancedGate;

    if (value.duplicate_check_freshness !== 'CURRENT') {
      return decision(
        MUTATION_DECISIONS.ABSTAIN,
        MUTATION_REASONS.STALE_DUPLICATE_CHECK,
        'best-effort create requires a fresh duplicate check'
      );
    }

    if (value.duplicate_match_found !== true && value.duplicate_match_found !== false) {
      return decision(
        MUTATION_DECISIONS.ABSTAIN,
        MUTATION_REASONS.DEDUPE_REQUIRED,
        'best-effort create requires an explicit duplicate-check result'
      );
    }

    if (value.duplicate_match_found === true) {
      return decision(
        MUTATION_DECISIONS.NO_OP,
        MUTATION_REASONS.DUPLICATE_EXISTS,
        'an equivalent durable resource already exists'
      );
    }

    return requireExpectedOutcome(value) || decision(MUTATION_DECISIONS.MUTATE_CANDIDATE);
  }

  return decision(
    MUTATION_DECISIONS.ABSTAIN,
    MUTATION_REASONS.DEDUPE_REQUIRED,
    'create mutation requires server idempotency or a fresh best-effort duplicate check'
  );
}

function classifyMutationGuard(proposal) {
  const value = proposal || {};

  if (!DURABLE_ADMISSIONS.has(value.admission_outcome)) {
    return decision(
      MUTATION_DECISIONS.ABSTAIN,
      MUTATION_REASONS.ADMISSION_REQUIRED,
      'durable mutation requires a durable admission outcome'
    );
  }

  if (!value.approved_operation || value.planned_operation !== value.approved_operation) {
    return decision(
      MUTATION_DECISIONS.ABSTAIN,
      MUTATION_REASONS.OPERATION_MISMATCH,
      'planned mutation operation must match the admitted operation'
    );
  }

  if (!value.approved_target || value.planned_target !== value.approved_target) {
    return decision(
      MUTATION_DECISIONS.ABSTAIN,
      MUTATION_REASONS.TARGET_MISMATCH,
      'planned mutation target must match the admitted target'
    );
  }

  if (value.scope_match !== true) {
    return decision(
      MUTATION_DECISIONS.ABSTAIN,
      MUTATION_REASONS.SCOPE_MISMATCH,
      'proposed mutation exceeds or cannot prove its authorized scope'
    );
  }

  if (value.mutation_kind === 'UPDATE_EXISTING') {
    return classifyUpdate(value);
  }

  if (value.mutation_kind === 'CREATE_RESOURCE') {
    return classifyCreate(value);
  }

  return decision(
    MUTATION_DECISIONS.ABSTAIN,
    MUTATION_REASONS.MUTATION_KIND_REQUIRED,
    'mutation kind must be explicitly UPDATE_EXISTING or CREATE_RESOURCE'
  );
}

function summarizeMutationShadow(observations) {
  if (!Array.isArray(observations) || observations.length === 0) {
    throw new TypeError('observations must be a non-empty array');
  }

  const avoidedNoOps = observations.filter(
    (entry) => entry.guard_outcome === MUTATION_DECISIONS.NO_OP && entry.actual_write_would_be_noop === true
  ).length;
  const falseBlocks = observations.filter(
    (entry) => entry.guard_outcome !== MUTATION_DECISIONS.MUTATE_CANDIDATE && entry.actual_write_was_necessary === true
  ).length;
  const routingBlocks = observations.filter(
    (entry) =>
      entry.guard_reason === MUTATION_REASONS.OPERATION_MISMATCH ||
      entry.guard_reason === MUTATION_REASONS.TARGET_MISMATCH ||
      entry.guard_reason === MUTATION_REASONS.PRECONDITION_MISMATCH
  ).length;
  const duplicateCreateBlocks = observations.filter(
    (entry) =>
      entry.guard_reason === MUTATION_REASONS.DUPLICATE_EXISTS ||
      entry.guard_reason === MUTATION_REASONS.AMBIGUOUS_RETRY
  ).length;
  const mutationCandidates = observations.filter(
    (entry) => entry.guard_outcome === MUTATION_DECISIONS.MUTATE_CANDIDATE
  ).length;

  return Object.freeze({
    total_observations: observations.length,
    avoided_noop_writes: avoidedNoOps,
    prevented_wrong_surface_mutations: routingBlocks,
    prevented_duplicate_create_risk: duplicateCreateBlocks,
    false_blocks: falseBlocks,
    mutation_candidates: mutationCandidates,
    actual_mutations_performed_by_shadow: 0,
    evidence_scope: 'PRE_MUTATION_SHADOW_ONLY',
  });
}

module.exports = {
  MUTATION_DECISIONS,
  MUTATION_REASONS,
  classifyMutationGuard,
  summarizeMutationShadow,
};
