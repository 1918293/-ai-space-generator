'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const { OUTCOMES } = require('./admission');
const {
  MUTATION_DECISIONS,
  MUTATION_REASONS,
  classifyMutationGuard,
  summarizeMutationShadow,
} = require('./pre-mutation-guard');

function updateProposal(overrides = {}) {
  return {
    admission_outcome: OUTCOMES.PERSIST_CANDIDATE,
    mutation_kind: 'UPDATE_EXISTING',
    update_strategy: 'STRONG_PRECONDITION',
    mutation_risk: 'MEDIUM',
    critical_effect: false,
    approved_operation: 'UPDATE_FILE',
    planned_operation: 'UPDATE_FILE',
    approved_target: 'prototype/action-admission/example.js',
    planned_target: 'prototype/action-admission/example.js',
    approved_precondition_token: 'blob-sha-v1',
    planned_precondition_token: 'blob-sha-v1',
    target_freshness: 'CURRENT',
    scope_match: true,
    current_content_hash: 'old',
    proposed_content_hash: 'new',
    has_meaningful_delta: true,
    approved_request_fingerprint: 'payload-v1',
    planned_request_fingerprint: 'payload-v1',
    prior_attempt_state: 'NONE',
    post_write_readback_required: true,
    expected_outcome: 'durable state changes as proposed',
    ...overrides,
  };
}

function createProposal(overrides = {}) {
  return {
    admission_outcome: OUTCOMES.PERSIST_CANDIDATE,
    mutation_kind: 'CREATE_RESOURCE',
    mutation_risk: 'MEDIUM',
    critical_effect: false,
    approved_operation: 'CREATE_ISSUE',
    planned_operation: 'CREATE_ISSUE',
    approved_target: 'repo:1918293/-ai-space-generator/issues',
    planned_target: 'repo:1918293/-ai-space-generator/issues',
    scope_match: true,
    approved_request_fingerprint: 'issue-payload-v1',
    planned_request_fingerprint: 'issue-payload-v1',
    create_dedupe_mode: 'FRESH_DUPLICATE_CHECK',
    prior_attempt_state: 'NONE',
    post_write_readback_required: true,
    duplicate_check_freshness: 'CURRENT',
    duplicate_match_found: false,
    expected_outcome: 'one matching issue exists and its receipt is returned',
    ...overrides,
  };
}

test('real no-op GitHub write incident is blocked as NO_OP', () => {
  const result = classifyMutationGuard(updateProposal({
    current_content_hash: 'same-content-sha',
    proposed_content_hash: 'same-content-sha',
    has_meaningful_delta: false,
  }));
  assert.equal(result.outcome, MUTATION_DECISIONS.NO_OP);
  assert.equal(result.reason, MUTATION_REASONS.NO_DELTA);
});

test('live wrong-tool incident is blocked by operation binding', () => {
  const result = classifyMutationGuard(updateProposal({
    approved_operation: 'UPDATE_PR_METADATA',
    planned_operation: 'UPDATE_FILE',
    approved_target: 'pull/13',
    planned_target: 'prototype/action-admission/pre-mutation-guard.test.js',
  }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.OPERATION_MISMATCH);
});

test('same operation aimed at the wrong target is blocked', () => {
  const result = classifyMutationGuard(updateProposal({
    approved_target: 'pull/13',
    planned_target: 'pull/12',
  }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.TARGET_MISMATCH);
});

test('strong update cannot swap the version token after admission', () => {
  const result = classifyMutationGuard(updateProposal({
    approved_precondition_token: 'blob-sha-v1',
    planned_precondition_token: 'blob-sha-v2',
  }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.PRECONDITION_MISMATCH);
});

test('strong update requires version token even when freshness says CURRENT', () => {
  const result = classifyMutationGuard(updateProposal({
    approved_precondition_token: null,
    planned_precondition_token: null,
  }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.PRECONDITION_MISMATCH);
});

test('Balanced permits low-risk best-effort metadata update with readback controls', () => {
  const result = classifyMutationGuard(updateProposal({
    update_strategy: 'BEST_EFFORT',
    mutation_risk: 'LOW',
    approved_operation: 'UPDATE_PR_METADATA',
    planned_operation: 'UPDATE_PR_METADATA',
    approved_target: 'pull/13',
    planned_target: 'pull/13',
    approved_precondition_token: null,
    planned_precondition_token: null,
  }));
  assert.equal(result.outcome, MUTATION_DECISIONS.MUTATE_CANDIDATE);
});

test('Balanced blocks high-risk best-effort update', () => {
  const result = classifyMutationGuard(updateProposal({
    update_strategy: 'BEST_EFFORT',
    mutation_risk: 'HIGH',
    approved_precondition_token: null,
    planned_precondition_token: null,
  }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.STRONG_PRECONDITION_REQUIRED);
});

test('Balanced blocks critical-effect best-effort update even at medium risk', () => {
  const result = classifyMutationGuard(updateProposal({
    update_strategy: 'BEST_EFFORT',
    critical_effect: true,
    approved_precondition_token: null,
    planned_precondition_token: null,
  }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.STRONG_PRECONDITION_REQUIRED);
});

test('best-effort update blocks ambiguous retry', () => {
  const result = classifyMutationGuard(updateProposal({
    update_strategy: 'BEST_EFFORT',
    approved_precondition_token: null,
    planned_precondition_token: null,
    prior_attempt_state: 'UNKNOWN',
  }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.AMBIGUOUS_RETRY);
});

test('best-effort update requires request fingerprint binding', () => {
  const result = classifyMutationGuard(updateProposal({
    update_strategy: 'BEST_EFFORT',
    approved_precondition_token: null,
    planned_precondition_token: null,
    planned_request_fingerprint: 'payload-v2',
  }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.REQUEST_MISMATCH);
});

test('best-effort update requires post-write readback', () => {
  const result = classifyMutationGuard(updateProposal({
    update_strategy: 'BEST_EFFORT',
    approved_precondition_token: null,
    planned_precondition_token: null,
    post_write_readback_required: false,
  }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.READBACK_REQUIRED);
});

test('missing durable admission cannot reach mutation layer', () => {
  const result = classifyMutationGuard(updateProposal({ admission_outcome: OUTCOMES.EXECUTE }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.ADMISSION_REQUIRED);
});

test('mutation kind must be explicit', () => {
  const result = classifyMutationGuard(updateProposal({ mutation_kind: '' }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.MUTATION_KIND_REQUIRED);
});

test('stale target requires fresh read before update', () => {
  const result = classifyMutationGuard(updateProposal({ target_freshness: 'STALE' }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.STALE_TARGET);
});

test('scope mismatch blocks mutation even when admission is durable', () => {
  const result = classifyMutationGuard(updateProposal({ scope_match: false }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.SCOPE_MISMATCH);
});

test('no meaningful update delta becomes NO_OP even without comparable hashes', () => {
  const result = classifyMutationGuard(updateProposal({
    current_content_hash: null,
    proposed_content_hash: null,
    has_meaningful_delta: false,
  }));
  assert.equal(result.outcome, MUTATION_DECISIONS.NO_OP);
  assert.equal(result.reason, MUTATION_REASONS.NO_DELTA);
});

test('semantic delta assertion cannot replace comparable update-state evidence', () => {
  const result = classifyMutationGuard(updateProposal({
    current_content_hash: null,
    proposed_content_hash: null,
    has_meaningful_delta: true,
  }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.DELTA_EVIDENCE_REQUIRED);
});

test('missing expected observable outcome blocks update', () => {
  const result = classifyMutationGuard(updateProposal({ expected_outcome: '' }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.EXPECTED_OUTCOME_REQUIRED);
});

test('fresh scoped meaningful strong update becomes mutation candidate', () => {
  const result = classifyMutationGuard(updateProposal());
  assert.equal(result.outcome, MUTATION_DECISIONS.MUTATE_CANDIDATE);
  assert.equal(result.reason, null);
});

test('live Issue #12 style best-effort create passes after fresh duplicate check', () => {
  const result = classifyMutationGuard(createProposal());
  assert.equal(result.outcome, MUTATION_DECISIONS.MUTATE_CANDIDATE);
  assert.equal(result.reason, null);
});

test('best-effort create becomes NO_OP when equivalent resource already exists', () => {
  const result = classifyMutationGuard(createProposal({ duplicate_match_found: true }));
  assert.equal(result.outcome, MUTATION_DECISIONS.NO_OP);
  assert.equal(result.reason, MUTATION_REASONS.DUPLICATE_EXISTS);
});

test('best-effort create blocks high-risk operation without server idempotency', () => {
  const result = classifyMutationGuard(createProposal({ mutation_risk: 'HIGH' }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.STRONG_PRECONDITION_REQUIRED);
});

test('best-effort create blocks retry when prior attempt outcome is unknown', () => {
  const result = classifyMutationGuard(createProposal({ prior_attempt_state: 'UNKNOWN' }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.AMBIGUOUS_RETRY);
});

test('best-effort create requires explicit duplicate-check result', () => {
  const result = classifyMutationGuard(createProposal({ duplicate_match_found: undefined }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.DEDUPE_REQUIRED);
});

test('best-effort create requires fresh duplicate check', () => {
  const result = classifyMutationGuard(createProposal({ duplicate_check_freshness: 'STALE' }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.STALE_DUPLICATE_CHECK);
});

test('create request fingerprint cannot change after admission', () => {
  const result = classifyMutationGuard(createProposal({ planned_request_fingerprint: 'other-payload' }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.REQUEST_MISMATCH);
});

test('server-idempotent create allows safe retry with the same key and request', () => {
  const result = classifyMutationGuard(createProposal({
    create_dedupe_mode: 'SERVER_IDEMPOTENCY',
    mutation_risk: 'HIGH',
    approved_idempotency_key: 'idem-123',
    planned_idempotency_key: 'idem-123',
    prior_attempt_state: 'UNKNOWN',
    duplicate_check_freshness: undefined,
    duplicate_match_found: undefined,
  }));
  assert.equal(result.outcome, MUTATION_DECISIONS.MUTATE_CANDIDATE);
});

test('server-idempotent create blocks key substitution', () => {
  const result = classifyMutationGuard(createProposal({
    create_dedupe_mode: 'SERVER_IDEMPOTENCY',
    approved_idempotency_key: 'idem-123',
    planned_idempotency_key: 'idem-456',
  }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.IDEMPOTENCY_MISMATCH);
});

test('create without a dedupe mode is blocked', () => {
  const result = classifyMutationGuard(createProposal({ create_dedupe_mode: '' }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.DEDUPE_REQUIRED);
});

test('shadow summary counts no-op, wrong-surface, and duplicate-create prevention', () => {
  const observations = [
    {
      guard_outcome: MUTATION_DECISIONS.NO_OP,
      guard_reason: MUTATION_REASONS.NO_DELTA,
      actual_write_would_be_noop: true,
      actual_write_was_necessary: false,
    },
    {
      guard_outcome: MUTATION_DECISIONS.ABSTAIN,
      guard_reason: MUTATION_REASONS.OPERATION_MISMATCH,
      actual_write_would_be_noop: true,
      actual_write_was_necessary: false,
    },
    {
      guard_outcome: MUTATION_DECISIONS.ABSTAIN,
      guard_reason: MUTATION_REASONS.PRECONDITION_MISMATCH,
      actual_write_would_be_noop: false,
      actual_write_was_necessary: false,
    },
    {
      guard_outcome: MUTATION_DECISIONS.ABSTAIN,
      guard_reason: MUTATION_REASONS.AMBIGUOUS_RETRY,
      actual_write_would_be_noop: false,
      actual_write_was_necessary: false,
    },
    {
      guard_outcome: MUTATION_DECISIONS.MUTATE_CANDIDATE,
      guard_reason: null,
      actual_write_would_be_noop: false,
      actual_write_was_necessary: true,
    },
  ];
  const summary = summarizeMutationShadow(observations);
  assert.equal(summary.total_observations, 5);
  assert.equal(summary.avoided_noop_writes, 1);
  assert.equal(summary.prevented_wrong_surface_mutations, 2);
  assert.equal(summary.prevented_duplicate_create_risk, 1);
  assert.equal(summary.false_blocks, 0);
  assert.equal(summary.mutation_candidates, 1);
  assert.equal(summary.actual_mutations_performed_by_shadow, 0);
});
