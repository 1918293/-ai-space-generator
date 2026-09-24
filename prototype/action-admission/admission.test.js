'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const {
  OUTCOMES,
  GAPS,
  classifyAdmission,
  reviseInterpretation,
  closeOutcome,
} = require('./admission');

function record(overrides = {}) {
  return {
    intent: 'continue',
    scope: 'CURRENT_TASK',
    basis: 'EXPLICIT',
    authority: 'SESSION',
    authority_freshness: 'CURRENT',
    risk: 'LOW',
    verification_required: false,
    verification_available: true,
    ...overrides,
  };
}

const realCases = [
  {
    name: 'Auto Loop continues the current task without durable promotion',
    input: record({ intent: 'continue_auto_loop' }),
    expected: OUTCOMES.EXECUTE,
  },
  {
    name: '@XMemo authorizes relevant invocation but not persistence',
    input: record({ intent: 'invoke_xmemo' }),
    expected: OUTCOMES.EXECUTE,
  },
  {
    name: '繼續 means continuation, not approval',
    input: record({ intent: 'continue' }),
    expected: OUTCOMES.EXECUTE,
  },
  {
    name: 'EXP + Auto + named tools remains current-task execution',
    input: record({ intent: 'continue_exp_with_requested_tools' }),
    expected: OUTCOMES.EXECUTE,
  },
  {
    name: 'plugin selection inferred from suitability remains non-persistent',
    input: record({ intent: 'select_relevant_plugins', basis: 'DERIVED' }),
    expected: OUTCOMES.EXECUTE_NONPERSISTENT,
  },
  {
    name: '授權執行 permits a bounded durable engineering work item as candidate',
    input: record({
      intent: 'create_bounded_implementation_work_item',
      authority: 'DURABLE',
      risk: 'MEDIUM',
    }),
    expected: OUTCOMES.PERSIST_CANDIDATE,
  },
];

for (const entry of realCases) {
  test(entry.name, () => {
    assert.equal(classifyAdmission(entry.input).outcome, entry.expected);
  });
}

test('missing scope produces a specification gap', () => {
  const result = classifyAdmission(record({ scope: '' }));
  assert.equal(result.outcome, OUTCOMES.ABSTAIN);
  assert.equal(result.reason, GAPS.SPECIFICATION);
});

test('missing required verification produces a verification gap', () => {
  const result = classifyAdmission(record({
    verification_required: true,
    verification_available: false,
  }));
  assert.equal(result.outcome, OUTCOMES.ABSTAIN);
  assert.equal(result.reason, GAPS.VERIFICATION);
});

test('derived information cannot authorize durable persistence', () => {
  const result = classifyAdmission(record({
    basis: 'DERIVED',
    authority: 'DURABLE',
    risk: 'MEDIUM',
  }));
  assert.equal(result.outcome, OUTCOMES.ABSTAIN);
  assert.equal(result.reason, GAPS.AUTHORITY);
});

test('stale historical authority cannot authorize a durable action', () => {
  const result = classifyAdmission(record({
    basis: 'CURRENT_AUTHORITY',
    authority: 'DURABLE',
    authority_freshness: 'STALE',
    risk: 'MEDIUM',
  }));
  assert.equal(result.outcome, OUTCOMES.ABSTAIN);
  assert.equal(result.reason, GAPS.AUTHORITY);
});

test('high-risk execution requires explicit current authorization', () => {
  const result = classifyAdmission(record({
    basis: 'CURRENT_AUTHORITY',
    authority: 'SESSION',
    risk: 'HIGH',
  }));
  assert.equal(result.outcome, OUTCOMES.ABSTAIN);
  assert.equal(result.reason, GAPS.AUTHORITY);
});

test('explicit enforcement is a candidate, not automatic enforcement', () => {
  const result = classifyAdmission(record({
    basis: 'EXPLICIT',
    authority: 'ENFORCEMENT',
    risk: 'HIGH',
  }));
  assert.equal(result.outcome, OUTCOMES.ENFORCE_CANDIDATE);
});

test('correction creates a new revision without mutating the old interpretation', () => {
  const previous = Object.freeze({ revision: 1, intent: 'acceptance', scope: 'ARTIFACT' });
  const next = reviseInterpretation(previous, { intent: 'correction' });

  assert.deepEqual(previous, { revision: 1, intent: 'acceptance', scope: 'ARTIFACT' });
  assert.equal(next.revision, 2);
  assert.equal(next.supersedes_revision, 1);
  assert.equal(next.intent, 'correction');
});

test('tool success does not imply task or user-visible success', () => {
  const result = closeOutcome({
    execution_receipt: { status: 'SUCCESS' },
    expected_outcome: { outside_mask_diff: 0 },
    observed_outcome: { outside_mask_diff: 12 },
    user_visible_success: false,
  });

  assert.equal(result.tool_success, true);
  assert.equal(result.task_success, false);
  assert.equal(result.user_visible_success, false);
});

test('task outcome equality ignores object key insertion order', () => {
  const result = closeOutcome({
    execution_receipt: { status: 'SUCCESS' },
    expected_outcome: { status: 'PASS', metrics: { a: 1, b: 2 } },
    observed_outcome: { metrics: { b: 2, a: 1 }, status: 'PASS' },
    user_visible_success: true,
  });

  assert.equal(result.tool_success, true);
  assert.equal(result.task_success, true);
  assert.equal(result.user_visible_success, true);
});

test('stale current authority cannot authorize even session execution', () => {
  const result = classifyAdmission(record({
    intent: 'resume_auto_on_new_task',
    basis: 'CURRENT_AUTHORITY',
    authority: 'SESSION',
    authority_freshness: 'STALE',
    risk: 'LOW',
  }));
  assert.equal(result.outcome, OUTCOMES.ABSTAIN);
  assert.equal(result.reason, GAPS.AUTHORITY);
});
