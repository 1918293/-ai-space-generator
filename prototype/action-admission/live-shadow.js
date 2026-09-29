'use strict';

const { OUTCOMES, classifyAdmission } = require('./admission');

function observeLiveShadow({ observation_id, normalized_action, actual_outcome }) {
  if (!observation_id || !normalized_action || !actual_outcome) {
    throw new TypeError('observation_id, normalized_action, and actual_outcome are required');
  }

  const shadow = classifyAdmission(normalized_action);

  return Object.freeze({
    observation_id,
    shadow_outcome: shadow.outcome,
    shadow_reason: shadow.reason || null,
    actual_outcome,
    decision_match: shadow.outcome === actual_outcome,
    observer_interfered: false,
    persistence_performed: false,
    evidence_scope: 'LIVE_CONVERSATION_SHADOW',
  });
}

function summarizeLiveShadow(observations) {
  if (!Array.isArray(observations) || observations.length === 0) {
    throw new TypeError('observations must be a non-empty array');
  }

  const matches = observations.filter((entry) => entry.decision_match).length;
  const abstentions = observations.filter((entry) => entry.shadow_outcome === OUTCOMES.ABSTAIN).length;
  const durableCandidates = observations.filter((entry) =>
    entry.shadow_outcome === OUTCOMES.PERSIST_CANDIDATE ||
    entry.shadow_outcome === OUTCOMES.ENFORCE_CANDIDATE
  ).length;

  return Object.freeze({
    total_observations: observations.length,
    decision_matches: matches,
    decision_mismatches: observations.length - matches,
    shadow_abstentions: abstentions,
    shadow_durable_candidates: durableCandidates,
    observer_interference_events: observations.filter((entry) => entry.observer_interfered).length,
    observer_persistence_events: observations.filter((entry) => entry.persistence_performed).length,
    evidence_scope: 'LIVE_CONVERSATION_SHADOW',
    not_proven: Object.freeze([
      'repeat_correction_rate_reduction',
      'behavioral_yield_improvement',
      'production_enforcement_safety',
    ]),
  });
}

module.exports = { observeLiveShadow, summarizeLiveShadow };
