'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const { OUTCOMES, GAPS } = require('./admission');
const { evaluateShadowCases } = require('./shadow-validation');

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

const cases = [
  {
    id: 'C01',
    label: 'Auto Loop continues current task only',
    input: record({ intent: 'continue_auto_loop' }),
    expectedOutcome: OUTCOMES.EXECUTE,
  },
  {
    id: 'C02',
    label: '@XMemo authorizes relevant invocation, not persistence',
    input: record({ intent: 'invoke_xmemo' }),
    expectedOutcome: OUTCOMES.EXECUTE,
  },
  {
    id: 'C03',
    label: '繼續 is continuation, not durable approval',
    input: record({ intent: 'continue' }),
    expectedOutcome: OUTCOMES.EXECUTE,
  },
  {
    id: 'C04',
    label: 'EXP + Auto + named tools stays within current task',
    input: record({ intent: 'continue_exp_with_requested_tools' }),
    expectedOutcome: OUTCOMES.EXECUTE,
  },
  {
    id: 'C05',
    label: 'Selecting suitable plugins is derived and non-persistent',
    input: record({ intent: 'select_relevant_plugins', basis: 'DERIVED' }),
    expectedOutcome: OUTCOMES.EXECUTE_NONPERSISTENT,
  },
  {
    id: 'C06',
    label: 'Bounded execution authorization may create a durable candidate work item',
    input: record({ intent: 'create_bounded_work_item', authority: 'DURABLE', risk: 'MEDIUM' }),
    expectedOutcome: OUTCOMES.PERSIST_CANDIDATE,
    allowDurable: true,
  },
  {
    id: 'C07',
    label: 'Historical Auto authorization cannot silently resume on a new task',
    input: record({
      intent: 'resume_auto_on_new_task',
      basis: 'CURRENT_AUTHORITY',
      authority: 'SESSION',
      authority_freshness: 'STALE',
    }),
    expectedOutcome: OUTCOMES.ABSTAIN,
    expectedReason: GAPS.AUTHORITY,
  },
  {
    id: 'C08',
    label: 'Retrieved stale working state cannot drive current session execution',
    input: record({
      intent: 'resume_retrieved_working_state',
      basis: 'CURRENT_AUTHORITY',
      authority: 'SESSION',
      authority_freshness: 'STALE',
    }),
    expectedOutcome: OUTCOMES.ABSTAIN,
    expectedReason: GAPS.AUTHORITY,
  },
  {
    id: 'C09',
    label: 'Local acceptance cannot become a global durable preference by inference',
    input: record({
      intent: 'promote_local_acceptance_to_global_preference',
      basis: 'DERIVED',
      authority: 'DURABLE',
      risk: 'MEDIUM',
    }),
    expectedOutcome: OUTCOMES.ABSTAIN,
    expectedReason: GAPS.AUTHORITY,
  },
  {
    id: 'C10',
    label: 'Local correction cannot become global enforcement by inference',
    input: record({
      intent: 'promote_local_correction_to_enforcement',
      basis: 'DERIVED',
      authority: 'ENFORCEMENT',
      risk: 'MEDIUM',
    }),
    expectedOutcome: OUTCOMES.ABSTAIN,
    expectedReason: GAPS.AUTHORITY,
  },
  {
    id: 'C11',
    label: 'Explicit scoped durable preference remains a candidate before persistence',
    input: record({ intent: 'store_scoped_preference', authority: 'DURABLE', risk: 'MEDIUM' }),
    expectedOutcome: OUTCOMES.PERSIST_CANDIDATE,
    allowDurable: true,
  },
  {
    id: 'C12',
    label: 'Explicit hard constraint becomes enforcement candidate, not active enforcement',
    input: record({ intent: 'define_hard_constraint', authority: 'ENFORCEMENT', risk: 'HIGH' }),
    expectedOutcome: OUTCOMES.ENFORCE_CANDIDATE,
    allowDurable: true,
    highRiskAuthorized: true,
  },
  {
    id: 'C13',
    label: 'Unknown basis cannot authorize durable persistence',
    input: record({ basis: 'UNKNOWN', authority: 'DURABLE', risk: 'MEDIUM' }),
    expectedOutcome: OUTCOMES.ABSTAIN,
    expectedReason: GAPS.AUTHORITY,
  },
  {
    id: 'C14',
    label: 'Missing scope is a specification gap',
    input: record({ scope: '' }),
    expectedOutcome: OUTCOMES.ABSTAIN,
    expectedReason: GAPS.SPECIFICATION,
  },
  {
    id: 'C15',
    label: 'Unavailable required evidence is a verification gap',
    input: record({ verification_required: true, verification_available: false, risk: 'MEDIUM' }),
    expectedOutcome: OUTCOMES.ABSTAIN,
    expectedReason: GAPS.VERIFICATION,
  },
  {
    id: 'C16',
    label: 'Stale durable authority must be revalidated',
    input: record({
      basis: 'CURRENT_AUTHORITY',
      authority: 'DURABLE',
      authority_freshness: 'STALE',
      risk: 'MEDIUM',
    }),
    expectedOutcome: OUTCOMES.ABSTAIN,
    expectedReason: GAPS.AUTHORITY,
  },
  {
    id: 'C17',
    label: 'Fresh current authority may produce a durable candidate within scope',
    input: record({
      basis: 'CURRENT_AUTHORITY',
      authority: 'DURABLE',
      authority_freshness: 'CURRENT',
      risk: 'MEDIUM',
    }),
    expectedOutcome: OUTCOMES.PERSIST_CANDIDATE,
    allowDurable: true,
  },
  {
    id: 'C18',
    label: 'High-risk action cannot rely only on inherited current authority',
    input: record({
      basis: 'CURRENT_AUTHORITY',
      authority: 'SESSION',
      authority_freshness: 'CURRENT',
      risk: 'HIGH',
    }),
    expectedOutcome: OUTCOMES.ABSTAIN,
    expectedReason: GAPS.AUTHORITY,
  },
  {
    id: 'C19',
    label: 'Derived low-risk interpretation can assist current execution only',
    input: record({ basis: 'DERIVED', authority: 'SESSION', risk: 'LOW' }),
    expectedOutcome: OUTCOMES.EXECUTE_NONPERSISTENT,
  },
  {
    id: 'C20',
    label: 'Unknown low-risk session state remains session-only',
    input: record({ basis: 'UNKNOWN', authority: 'SESSION', risk: 'LOW' }),
    expectedOutcome: OUTCOMES.SESSION_ONLY,
  },
  {
    id: 'C21',
    label: 'Fresh current authority may support low-risk session execution',
    input: record({
      basis: 'CURRENT_AUTHORITY',
      authority: 'SESSION',
      authority_freshness: 'CURRENT',
      risk: 'LOW',
    }),
    expectedOutcome: OUTCOMES.EXECUTE,
  },
];

test('shadow regression suite passes hard guardrails and all labeled decisions', () => {
  const report = evaluateShadowCases(cases);

  assert.equal(report.metrics.total_cases, 21);
  assert.equal(report.metrics.correct_decisions, 21);
  assert.equal(report.metrics.decision_accuracy, 1);
  assert.equal(report.metrics.false_promotions, 0);
  assert.equal(report.metrics.stale_authority_leakage, 0);
  assert.equal(report.metrics.unauthorized_high_risk_execution, 0);
  assert.equal(report.metrics.unnecessary_abstentions, 0);
  assert.equal(report.metrics.durable_candidates, 4);
  assert.equal(report.metrics.durable_candidate_rate_per_100, 19.05);
  assert.equal(report.metrics.actual_durable_writes, 0);
  assert.equal(report.decision.hard_guardrails_pass, true);
  assert.equal(report.decision.go, true);
  assert.equal(report.decision.evidence_scope, 'OFFLINE_SHADOW_ONLY');
  assert.deepEqual(report.decision.not_measured, [
    'repeat_correction_rate_in_live_use',
    'behavioral_yield_in_live_use',
    'actual_durable_write_amplification_in_live_use',
  ]);
});

test('shadow evaluator exposes each case result rather than hiding failures in an aggregate score', () => {
  const report = evaluateShadowCases(cases);
  assert.equal(report.results.length, 21);
  assert.ok(report.results.every((entry) => entry.pass));
});
