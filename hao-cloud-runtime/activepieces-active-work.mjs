import { evaluateMaintenancePreflight } from './maintenance-preflight.mjs';

export const ACTIVEPIECES_SIGNAL_VERSION = '0.1.0-exp';

const ACTIVE_STATUSES = new Set(['RUNNING', 'QUEUED', 'PAUSED']);
const FAILED_STATUSES = new Set([
  'FAILED',
  'QUOTA_EXCEEDED',
  'INTERNAL_ERROR',
  'MEMORY_LIMIT_EXCEEDED',
  'TIMEOUT',
  'CANCELED',
  'LOG_SIZE_EXCEEDED',
]);

const TAG_PREFIX = 'hao:';

function asString(value) {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function parseTime(value) {
  const s = asString(value);
  if (!s) return null;
  const ms = Date.parse(s);
  return Number.isFinite(ms) ? ms : null;
}

export function parseHaoTags(tags = []) {
  const out = {};
  for (const raw of Array.isArray(tags) ? tags : []) {
    if (typeof raw !== 'string' || !raw.startsWith(TAG_PREFIX)) continue;
    const body = raw.slice(TAG_PREFIX.length);
    const idx = body.indexOf('=');
    if (idx < 1) continue;
    const key = body.slice(0, idx).trim();
    const value = body.slice(idx + 1).trim();
    if (key && value) out[key] = value;
  }
  return out;
}

function relationFor(meta, candidate) {
  const runKey = asString(meta['run-key']);
  const objective = asString(meta.objective);
  const target = asString(meta.target);
  const candidateRunKey = asString(candidate.runKey);
  const candidateObjective = asString(candidate.objective);
  const candidateTarget = asString(candidate.target);
  const dependencies = Array.isArray(candidate.dependsOnRunKeys)
    ? candidate.dependsOnRunKeys.filter((x) => typeof x === 'string')
    : [];

  if (candidateRunKey && runKey === candidateRunKey) return 'SAME_OBJECTIVE';
  if (candidateObjective && objective === candidateObjective) return 'SAME_OBJECTIVE';
  if (runKey && dependencies.includes(runKey)) return 'UPSTREAM_DEPENDENCY';
  if (candidateTarget && target === candidateTarget) return 'SAME_TARGET_DIFFERENT_OBJECTIVE';
  return 'INDEPENDENT';
}

function normalizedStatus(run, relation, meta, candidate, nowMs, staleAfterMs) {
  const providerStatus = asString(run.status) ?? 'UNKNOWN';
  const lastSeen = parseTime(run.updated) ?? parseTime(run.startTime) ?? parseTime(run.created);
  const isStale = ACTIVE_STATUSES.has(providerStatus) && lastSeen !== null && nowMs - lastSeen > staleAfterMs;
  if (isStale) return 'UNKNOWN';

  if (candidate.targetReadbackVerifiedSatisfied === true && relation === 'SAME_OBJECTIVE') {
    return 'COMPLETED';
  }
  if (ACTIVE_STATUSES.has(providerStatus)) return 'ACTIVE';
  if (providerStatus === 'SUCCEEDED') {
    const verified = meta.verified === 'true' || candidate.targetReadbackVerifiedSatisfied === true;
    return relation === 'SAME_OBJECTIVE' && !verified ? 'UNKNOWN' : 'COMPLETED';
  }
  if (FAILED_STATUSES.has(providerStatus)) return 'BLOCKED';
  return 'UNKNOWN';
}

export function normalizeActivepiecesRun(run = {}, candidate = {}, options = {}) {
  const nowMs = Number.isFinite(options.nowMs) ? options.nowMs : Date.now();
  const staleAfterMs = Number.isFinite(options.staleAfterMs) ? options.staleAfterMs : 15 * 60 * 1000;
  const meta = parseHaoTags(run.tags);
  const relation = relationFor(meta, candidate);
  const status = normalizedStatus(run, relation, meta, candidate, nowMs, staleAfterMs);
  const sameTarget = asString(meta.target) && asString(meta.target) === asString(candidate.target);
  const overlap = sameTarget && candidate.expectedDeltaOverlap === true;
  const verified = candidate.targetReadbackVerifiedSatisfied === true || meta.verified === 'true';
  const providerStatus = asString(run.status) ?? 'UNKNOWN';
  const blockerFingerprint = asString(meta['blocker-fingerprint']);
  const candidateBlockerFingerprint = asString(candidate.blockerFingerprint);
  const blockerChanged = Boolean(
    FAILED_STATUSES.has(providerStatus) &&
    blockerFingerprint &&
    candidateBlockerFingerprint &&
    blockerFingerprint !== candidateBlockerFingerprint
  );

  return {
    checked: true,
    status,
    relation,
    targetConflict: sameTarget && candidate.targetConflict === true,
    expectedDeltaOverlap: overlap,
    outcomeVerified: verified,
    source: 'ACTIVEPIECES_FLOW_RUN',
    runKey: asString(meta['run-key']),
    providerRunId: asString(run.id),
    providerStatus,
    projectId: asString(run.projectId),
    flowId: asString(run.flowId),
    lane: asString(meta.lane),
    target: asString(meta.target),
    objective: asString(meta.objective),
    blockerKnown: FAILED_STATUSES.has(providerStatus),
    blockerChanged,
    stale: status === 'UNKNOWN' && ACTIVE_STATUSES.has(providerStatus),
    tags: meta,
  };
}

function score(signal, candidate) {
  let value = 0;
  if (signal.runKey && signal.runKey === asString(candidate.runKey)) value += 100;
  if (signal.relation === 'SAME_OBJECTIVE') value += signal.status === 'ACTIVE' ? 80 : 70;
  if (signal.relation === 'UPSTREAM_DEPENDENCY') value += 60;
  if (signal.relation === 'SAME_TARGET_DIFFERENT_OBJECTIVE') value += 50;
  if (signal.status === 'UNKNOWN') value += 40;
  if (signal.relation === 'INDEPENDENT') value += 1;
  return value;
}

export function resolveActivepiecesSignal(runs = [], candidate = {}, options = {}) {
  if (!Array.isArray(runs) || runs.length === 0) {
    return {
      checked: true,
      status: 'NONE',
      relation: 'NONE',
      targetConflict: false,
      expectedDeltaOverlap: false,
      outcomeVerified: false,
      source: 'ACTIVEPIECES_FLOW_RUN_LIST_EMPTY',
      runKey: null,
      blockerKnown: false,
      blockerChanged: false,
    };
  }

  const signals = runs.map((run) => normalizeActivepiecesRun(run, candidate, options));
  signals.sort((a, b) => score(b, candidate) - score(a, candidate));
  return signals[0];
}

export function buildPreflightFromActivepieces({ runs = [], candidate = {}, preflight = {}, options = {} } = {}) {
  const signal = resolveActivepiecesSignal(runs, candidate, options);
  return evaluateMaintenancePreflight({
    provider: 'GENERIC',
    action: preflight.action ?? 'ACTIVEPIECES_ORCHESTRATED_ACTION',
    freshStateRead: preflight.freshStateRead === true,
    currentStateResolved: preflight.currentStateResolved === true,
    targetIdentityVerified: preflight.targetIdentityVerified === true,
    mutating: preflight.mutating === true,
    consequential: preflight.consequential === true,
    formalMutation: preflight.formalMutation === true,
    necessityEstablished: preflight.necessityEstablished === true,
    blockerKnown: signal.blockerKnown,
    blockerChanged: signal.blockerChanged,
    objectiveSatisfied: preflight.objectiveSatisfied === true,
    currentFingerprint: preflight.currentFingerprint,
    desiredFingerprint: preflight.desiredFingerprint,
    validationPlanReady: preflight.validationPlanReady === true,
    selectedValidationEvidence: preflight.selectedValidationEvidence,
    activeWork: signal,
  });
}

export const ACTIVEPIECES_ACTIVE_WORK_POLICY = Object.freeze({
  status: 'EXP_NON_AUTHORITY',
  providerRole: 'ACTIVE_WORK_SIGNAL_ONLY',
  authority: 'NONE',
  sourceFields: ['id', 'projectId', 'flowId', 'status', 'tags', 'created', 'updated', 'startTime', 'finishTime'],
  tagContract: [
    'hao:run-key=<RUN_KEY>',
    'hao:objective=<OBJECTIVE_ID>',
    'hao:target=<TARGET_ID>',
    'hao:lane=<EXECUTION_LANE>',
    'hao:verified=true',
    'hao:blocker-fingerprint=<FINGERPRINT>',
  ],
  rules: [
    'RUNNING|QUEUED|PAUSED + SAME_OBJECTIVE => WAIT',
    'SUCCEEDED + SAME_OBJECTIVE + VERIFIED => NO_OP',
    'SUCCEEDED + SAME_OBJECTIVE + NO_VERIFICATION => FAIL_CLOSED',
    'FAILED_LIKE + UNCHANGED_BLOCKER => WAIT_TRIGGER',
    'FAILED_LIKE + TARGET_READBACK_ALREADY_SATISFIED => NO_OP',
    'SAME_TARGET_DIFFERENT_OBJECTIVE + OVERLAP_OR_CONFLICT => WAIT',
    'STALE_ACTIVE_PROVIDER_SIGNAL => FAIL_CLOSED',
  ],
  noGlobalTaskDatabase: true,
  formalMutationStillRequiresSingleWriteGateway: true,
});
