'use strict';

const OUTCOMES = Object.freeze({
  EXECUTE: 'EXECUTE',
  EXECUTE_NONPERSISTENT: 'EXECUTE_NONPERSISTENT',
  SESSION_ONLY: 'SESSION_ONLY',
  PERSIST_CANDIDATE: 'PERSIST_CANDIDATE',
  ENFORCE_CANDIDATE: 'ENFORCE_CANDIDATE',
  ABSTAIN: 'ABSTAIN',
});

const GAPS = Object.freeze({
  SPECIFICATION: 'SPECIFICATION_GAP',
  VERIFICATION: 'VERIFICATION_GAP',
  AUTHORITY: 'AUTHORITY_GAP',
});

const BASIS = new Set(['EXPLICIT', 'CURRENT_AUTHORITY', 'DERIVED', 'UNKNOWN']);
const AUTHORITY = new Set(['SESSION', 'DURABLE', 'ENFORCEMENT']);
const RISK = new Set(['LOW', 'MEDIUM', 'HIGH']);
const FRESHNESS = new Set(['CURRENT', 'STALE', 'UNKNOWN']);

function abstain(reason, details) {
  return Object.freeze({ outcome: OUTCOMES.ABSTAIN, reason, details });
}

function classifyAdmission(record) {
  const value = record || {};

  if (!value.intent || !value.scope) {
    return abstain(GAPS.SPECIFICATION, 'intent and scope are required');
  }

  if (!BASIS.has(value.basis) || !AUTHORITY.has(value.authority) || !RISK.has(value.risk)) {
    return abstain(GAPS.SPECIFICATION, 'basis, authority, or risk is invalid');
  }

  if (value.verification_required === true && value.verification_available !== true) {
    return abstain(GAPS.VERIFICATION, 'required verification evidence is unavailable');
  }

  const freshness = value.authority_freshness || 'UNKNOWN';
  if (!FRESHNESS.has(freshness)) {
    return abstain(GAPS.SPECIFICATION, 'authority_freshness is invalid');
  }

  if (value.authority !== 'SESSION' && value.basis === 'DERIVED') {
    return abstain(GAPS.AUTHORITY, 'derived interpretation cannot authorize durable effects');
  }

  if (value.authority !== 'SESSION' && value.basis === 'UNKNOWN') {
    return abstain(GAPS.AUTHORITY, 'unknown basis cannot authorize durable effects');
  }

  if (
    value.basis === 'CURRENT_AUTHORITY' &&
    value.authority !== 'SESSION' &&
    freshness !== 'CURRENT'
  ) {
    return abstain(GAPS.AUTHORITY, 'historical or stale authority must be revalidated');
  }

  if (value.risk === 'HIGH' && value.basis !== 'EXPLICIT') {
    return abstain(GAPS.AUTHORITY, 'high-risk actions require explicit current authorization');
  }

  if (value.authority === 'ENFORCEMENT') {
    if (value.basis !== 'EXPLICIT' && value.basis !== 'CURRENT_AUTHORITY') {
      return abstain(GAPS.AUTHORITY, 'runtime enforcement requires explicit or current authority');
    }
    return Object.freeze({ outcome: OUTCOMES.ENFORCE_CANDIDATE, reason: null, details: null });
  }

  if (value.authority === 'DURABLE') {
    return Object.freeze({ outcome: OUTCOMES.PERSIST_CANDIDATE, reason: null, details: null });
  }

  if (value.basis === 'DERIVED') {
    return Object.freeze({ outcome: OUTCOMES.EXECUTE_NONPERSISTENT, reason: null, details: null });
  }

  if (value.basis === 'UNKNOWN') {
    return Object.freeze({ outcome: OUTCOMES.SESSION_ONLY, reason: null, details: null });
  }

  return Object.freeze({ outcome: OUTCOMES.EXECUTE, reason: null, details: null });
}

function reviseInterpretation(previous, patch) {
  if (!previous || !Number.isInteger(previous.revision) || previous.revision < 1) {
    throw new TypeError('previous interpretation must have a positive integer revision');
  }

  const next = {
    ...previous,
    ...patch,
    revision: previous.revision + 1,
    supersedes_revision: previous.revision,
  };

  return Object.freeze(next);
}

function closeOutcome({ execution_receipt, expected_outcome, observed_outcome, user_visible_success }) {
  const tool_success = execution_receipt?.status === 'SUCCESS';
  const task_success = JSON.stringify(expected_outcome) === JSON.stringify(observed_outcome);

  return Object.freeze({
    tool_success,
    task_success,
    user_visible_success: user_visible_success === true,
  });
}

module.exports = {
  OUTCOMES,
  GAPS,
  classifyAdmission,
  reviseInterpretation,
  closeOutcome,
};
