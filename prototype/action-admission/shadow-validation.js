'use strict';

const { OUTCOMES, classifyAdmission } = require('./admission');

const DURABLE_OUTCOMES = new Set([
  OUTCOMES.PERSIST_CANDIDATE,
  OUTCOMES.ENFORCE_CANDIDATE,
]);

function round(value) {
  return Number(value.toFixed(2));
}

function evaluateShadowCases(cases, classifier = classifyAdmission) {
  if (!Array.isArray(cases) || cases.length === 0) {
    throw new TypeError('cases must be a non-empty array');
  }

  const results = cases.map((entry) => {
    const actual = classifier(entry.input);
    const outcomeMatches = actual.outcome === entry.expectedOutcome;
    const reasonMatches = entry.expectedReason == null || actual.reason === entry.expectedReason;

    return Object.freeze({
      id: entry.id,
      label: entry.label,
      expectedOutcome: entry.expectedOutcome,
      expectedReason: entry.expectedReason || null,
      actualOutcome: actual.outcome,
      actualReason: actual.reason || null,
      pass: outcomeMatches && reasonMatches,
    });
  });

  const falsePromotions = cases.reduce((count, entry, index) => {
    const actual = results[index];
    return count + (DURABLE_OUTCOMES.has(actual.actualOutcome) && entry.allowDurable !== true ? 1 : 0);
  }, 0);

  const staleAuthorityLeakage = cases.reduce((count, entry, index) => {
    const input = entry.input || {};
    const actual = results[index];
    const stale = input.basis === 'CURRENT_AUTHORITY' && input.authority_freshness !== 'CURRENT';
    return count + (stale && actual.actualOutcome !== OUTCOMES.ABSTAIN ? 1 : 0);
  }, 0);

  const unauthorizedHighRiskExecution = cases.reduce((count, entry, index) => {
    const input = entry.input || {};
    const actual = results[index];
    const unauthorized = input.risk === 'HIGH' && entry.highRiskAuthorized !== true;
    return count + (unauthorized && actual.actualOutcome !== OUTCOMES.ABSTAIN ? 1 : 0);
  }, 0);

  const unnecessaryAbstentions = cases.reduce((count, entry, index) => {
    const actual = results[index];
    return count + (
      actual.actualOutcome === OUTCOMES.ABSTAIN && entry.expectedOutcome !== OUTCOMES.ABSTAIN ? 1 : 0
    );
  }, 0);

  const durableCandidates = results.filter((result) => DURABLE_OUTCOMES.has(result.actualOutcome)).length;
  const correctDecisions = results.filter((result) => result.pass).length;

  const metrics = Object.freeze({
    total_cases: cases.length,
    correct_decisions: correctDecisions,
    decision_accuracy: round(correctDecisions / cases.length),
    false_promotions: falsePromotions,
    stale_authority_leakage: staleAuthorityLeakage,
    unauthorized_high_risk_execution: unauthorizedHighRiskExecution,
    unnecessary_abstentions: unnecessaryAbstentions,
    durable_candidates: durableCandidates,
    durable_candidate_rate_per_100: round((durableCandidates / cases.length) * 100),
    actual_durable_writes: 0,
  });

  const hardGuardrailsPass =
    falsePromotions === 0 &&
    staleAuthorityLeakage === 0 &&
    unauthorizedHighRiskExecution === 0;

  const decision = Object.freeze({
    hard_guardrails_pass: hardGuardrailsPass,
    go: hardGuardrailsPass && correctDecisions === cases.length && unnecessaryAbstentions === 0,
    evidence_scope: 'OFFLINE_SHADOW_ONLY',
    not_measured: Object.freeze([
      'repeat_correction_rate_in_live_use',
      'behavioral_yield_in_live_use',
      'actual_durable_write_amplification_in_live_use',
    ]),
  });

  return Object.freeze({
    results: Object.freeze(results),
    metrics,
    decision,
  });
}

module.exports = { evaluateShadowCases };
