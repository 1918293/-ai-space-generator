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
    expected_outcome: 'durable state changes as proposed',
    ...overrides,
  };
}

function createProposal(overrides = {}) {
  return {
    admission_outcome: OUTCOMES.PERSIST_CANDIDATE,
    mutation_kind: 'CREATE_RESOURCE',
    approved_operation: 'CREATE_ISSUE',
    planned_operation: 'CREATE_ISSUE',
    approved_target: 'repo:1918293/-ai-space-generator/issues',
    planned_target: 'repo:1918293/-ai-space-generator/issues',
    scope_match: true,
    approved_request_fingerprint: 'issue-payload-v1',
    planned_request_fingerprint: 'issue-payload-v1',
    create_dedupe_mode: 'FRESH_DUPLICATE_CHECK',
    prior_attempt_state: 'NONE',
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

test('planned update cannot swap the version token after admission', () => {
  const result = classifyMutationGuard(updateProposal({
    approved_precondition_token: 'blob-sha-v1',
    planned_precondition_token: 'blob-sha-v2',
  }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.PRECONDITION_MISMATCH);
});

test('missing version token cannot be replaced by a CURRENT freshness claim', () => {
  const result = classifyMutationGuard(updateProposal({
    approved_precondition_token: null,
    planned_precondition_token: null,
    target_freshness: 'CURRENT',
  }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.PRECONDITION_MISMATCH);
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

test('fresh scoped meaningful update becomes mutation candidate', () => {
  const result = classifyMutationGuard(updateProposal());
  assert.equal(result.outcome, MUTATION_DECISIONS.MUTATE_CANDIDATE);
  assert.equal(result.reason, null);
});

test('enforcement candidate still passes through guard rather than auto-enforcing', () => {
  const result = classifyMutationGuard(updateProposal({ admission_outcome: OUTCOMES.ENFORCE_CANDIDATE }));
  assert.equal(result.outcome, MUTATION_DECISIONS.MUTATE_CANDIDATE);
});

test('live Issue #12 style create passes after fresh duplicate check', () => {
  const result = classifyMutationGuard(createProposal());
  assert.equal(result.outcome, MUTATION_DECISIONS.MUTATE_CANDIDATE);
  assert.equal(result.reason, null);
});

test('best-effort create becomes NO_OP when equivalent resource already exists', () => {
  const result = classifyMutationGuard(createProposal({ duplicate_match_found: true }));
  assert.equal(result.outcome, MUTATION_DECISIONS.NO_OP);
  assert.equal(result.reason, MUTATION_REASONS.DUPLICATE_EXISTS);
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
