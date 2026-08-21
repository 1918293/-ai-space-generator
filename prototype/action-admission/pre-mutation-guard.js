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
  PRECONDITION_MISMATCH: 'PRECONDITION_MISMATCH',
  STALE_TARGET: 'STALE_TARGET',
  SCOPE_MISMATCH: 'SCOPE_MISMATCH',
  NO_DELTA: 'NO_DELTA',
  DELTA_EVIDENCE_REQUIRED: 'DELTA_EVIDENCE_REQUIRED',
  EXPECTED_OUTCOME_REQUIRED: 'EXPECTED_OUTCOME_REQUIRED',
});

const DURABLE_ADMISSIONS = new Set([
  OUTCOMES.PERSIST_CANDIDATE,
  OUTCOMES.ENFORCE_CANDIDATE,
]);

function decision(outcome, reason = null, details = null) {
  return Object.freeze({ outcome, reason, details });
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

  if (value.target_freshness !== 'CURRENT') {
    return decision(
      MUTATION_DECISIONS.ABSTAIN,
      MUTATION_REASONS.STALE_TARGET,
      'target must be freshly read before mutation'
    );
  }

  if (value.scope_match !== true) {
    return decision(
      MUTATION_DECISIONS.ABSTAIN,
      MUTATION_REASONS.SCOPE_MISMATCH,
      'proposed mutation exceeds or cannot prove its authorized scope'
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

  if (!value.expected_outcome) {
    return decision(
      MUTATION_DECISIONS.ABSTAIN,
      MUTATION_REASONS.EXPECTED_OUTCOME_REQUIRED,
      'expected observable outcome is required before mutation'
    );
  }

  return decision(MUTATION_DECISIONS.MUTATE_CANDIDATE);
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
  const mutationCandidates = observations.filter(
    (entry) => entry.guard_outcome === MUTATION_DECISIONS.MUTATE_CANDIDATE
  ).length;

  return Object.freeze({
    total_observations: observations.length,
    avoided_noop_writes: avoidedNoOps,
    prevented_wrong_surface_mutations: routingBlocks,
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
