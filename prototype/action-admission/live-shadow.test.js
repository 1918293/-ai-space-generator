'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const { OUTCOMES } = require('./admission');
const { observeLiveShadow, summarizeLiveShadow } = require('./live-shadow');

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

test('live shadow observes current Auto Loop without interference', () => {
  const observation = observeLiveShadow({
    observation_id: 'LIVE-01-AUTO-LOOP',
    normalized_action: record({ intent: 'continue_auto_loop' }),
    actual_outcome: OUTCOMES.EXECUTE,
  });

  assert.equal(observation.shadow_outcome, OUTCOMES.EXECUTE);
  assert.equal(observation.decision_match, true);
  assert.equal(observation.observer_interfered, false);
  assert.equal(observation.persistence_performed, false);
});

test('live shadow matches bounded execution authorization without broadening it', () => {
  const observation = observeLiveShadow({
    observation_id: 'LIVE-02-BOUNDED-AUTHORIZATION',
    normalized_action: record({
      intent: 'create_bounded_implementation_work_item',
      authority: 'DURABLE',
      risk: 'MEDIUM',
    }),
    actual_outcome: OUTCOMES.PERSIST_CANDIDATE,
  });

  assert.equal(observation.shadow_outcome, OUTCOMES.PERSIST_CANDIDATE);
  assert.equal(observation.decision_match, true);
  assert.equal(observation.persistence_performed, false);
});

test('live shadow matches a low-risk recommendation request as execution only', () => {
  const observation = observeLiveShadow({
    observation_id: 'LIVE-03-SCOPE-RECOMMENDATION',
    normalized_action: record({ intent: 'recommend_high_value_scope' }),
    actual_outcome: OUTCOMES.EXECUTE,
  });

  assert.equal(observation.shadow_outcome, OUTCOMES.EXECUTE);
  assert.equal(observation.decision_match, true);
});

test('live shadow summary remains descriptive and does not claim behavioral yield', () => {
  const observations = [
    observeLiveShadow({
      observation_id: 'LIVE-01-AUTO-LOOP',
      normalized_action: record({ intent: 'continue_auto_loop' }),
      actual_outcome: OUTCOMES.EXECUTE,
    }),
    observeLiveShadow({
      observation_id: 'LIVE-02-BOUNDED-AUTHORIZATION',
      normalized_action: record({
        intent: 'create_bounded_implementation_work_item',
        authority: 'DURABLE',
        risk: 'MEDIUM',
      }),
      actual_outcome: OUTCOMES.PERSIST_CANDIDATE,
    }),
    observeLiveShadow({
      observation_id: 'LIVE-03-SCOPE-RECOMMENDATION',
      normalized_action: record({ intent: 'recommend_high_value_scope' }),
      actual_outcome: OUTCOMES.EXECUTE,
    }),
  ];

  const summary = summarizeLiveShadow(observations);

  assert.equal(summary.total_observations, 3);
  assert.equal(summary.decision_matches, 3);
  assert.equal(summary.decision_mismatches, 0);
  assert.equal(summary.shadow_abstentions, 0);
  assert.equal(summary.shadow_durable_candidates, 1);
  assert.equal(summary.observer_interference_events, 0);
  assert.equal(summary.observer_persistence_events, 0);
  assert.deepEqual(summary.not_proven, [
    'repeat_correction_rate_reduction',
    'behavioral_yield_improvement',
    'production_enforcement_safety',
  ]);
});
