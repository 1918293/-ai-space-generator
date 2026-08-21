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

function proposal(overrides = {}) {
  return {
    admission_outcome: OUTCOMES.PERSIST_CANDIDATE,
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

test('real no-op GitHub write incident is blocked as NO_OP', () => {
  const result = classifyMutationGuard(proposal({
    current_content_hash: 'same-content-sha',
    proposed_content_hash: 'same-content-sha',
    has_meaningful_delta: false,
  }));
  assert.equal(result.outcome, MUTATION_DECISIONS.NO_OP);
  assert.equal(result.reason, MUTATION_REASONS.NO_DELTA);
});

test('live wrong-tool incident is blocked by operation binding', () => {
  const result = classifyMutationGuard(proposal({
    approved_operation: 'UPDATE_PR_METADATA',
    planned_operation: 'UPDATE_FILE',
    approved_target: 'pull/13',
    planned_target: 'prototype/action-admission/pre-mutation-guard.test.js',
  }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.OPERATION_MISMATCH);
});

test('same operation aimed at the wrong target is blocked', () => {
  const result = classifyMutationGuard(proposal({
    approved_target: 'pull/13',
    planned_target: 'pull/12',
  }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.TARGET_MISMATCH);
});

test('planned mutation cannot swap the version token after admission', () => {
  const result = classifyMutationGuard(proposal({
    approved_precondition_token: 'blob-sha-v1',
    planned_precondition_token: 'blob-sha-v2',
  }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.PRECONDITION_MISMATCH);
});

test('missing version token cannot be replaced by a CURRENT freshness claim', () => {
  const result = classifyMutationGuard(proposal({
    approved_precondition_token: null,
    planned_precondition_token: null,
    target_freshness: 'CURRENT',
  }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.PRECONDITION_MISMATCH);
});

test('missing durable admission cannot reach mutation layer', () => {
  const result = classifyMutationGuard(proposal({ admission_outcome: OUTCOMES.EXECUTE }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.ADMISSION_REQUIRED);
});

test('stale target requires fresh read before mutation', () => {
  const result = classifyMutationGuard(proposal({ target_freshness: 'STALE' }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.STALE_TARGET);
});

test('scope mismatch blocks mutation even when admission is durable', () => {
  const result = classifyMutationGuard(proposal({ scope_match: false }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.SCOPE_MISMATCH);
});

test('no meaningful delta becomes NO_OP even without comparable hashes', () => {
  const result = classifyMutationGuard(proposal({
    current_content_hash: null,
    proposed_content_hash: null,
    has_meaningful_delta: false,
  }));
  assert.equal(result.outcome, MUTATION_DECISIONS.NO_OP);
  assert.equal(result.reason, MUTATION_REASONS.NO_DELTA);
});

test('semantic delta assertion cannot replace comparable state evidence', () => {
  const result = classifyMutationGuard(proposal({
    current_content_hash: null,
    proposed_content_hash: null,
    has_meaningful_delta: true,
  }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.DELTA_EVIDENCE_REQUIRED);
});

test('missing expected observable outcome blocks mutation', () => {
  const result = classifyMutationGuard(proposal({ expected_outcome: '' }));
  assert.equal(result.outcome, MUTATION_DECISIONS.ABSTAIN);
  assert.equal(result.reason, MUTATION_REASONS.EXPECTED_OUTCOME_REQUIRED);
});

test('fresh scoped meaningful durable change becomes mutation candidate', () => {
  const result = classifyMutationGuard(proposal());
  assert.equal(result.outcome, MUTATION_DECISIONS.MUTATE_CANDIDATE);
  assert.equal(result.reason, null);
});

test('enforcement candidate still passes through mutation guard rather than auto-enforcing', () => {
  const result = classifyMutationGuard(proposal({ admission_outcome: OUTCOMES.ENFORCE_CANDIDATE }));
  assert.equal(result.outcome, MUTATION_DECISIONS.MUTATE_CANDIDATE);
});

test('shadow summary counts no-op, wrong-surface, and precondition prevention', () => {
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
      guard_outcome: MUTATION_DECISIONS.MUTATE_CANDIDATE,
      guard_reason: null,
      actual_write_would_be_noop: false,
      actual_write_was_necessary: true,
    },
  ];
  const summary = summarizeMutationShadow(observations);
  assert.equal(summary.total_observations, 4);
  assert.equal(summary.avoided_noop_writes, 1);
  assert.equal(summary.prevented_wrong_surface_mutations, 2);
  assert.equal(summary.false_blocks, 0);
  assert.equal(summary.mutation_candidates, 1);
  assert.equal(summary.actual_mutations_performed_by_shadow, 0);
});
