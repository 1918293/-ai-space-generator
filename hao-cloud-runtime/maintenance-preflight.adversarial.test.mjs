import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import {
  evaluateMaintenancePreflight,
  PREFLIGHT_DECISIONS,
  PREFLIGHT_PROVIDERS,
} from './maintenance-preflight.mjs';

const none = { checked: true, status: 'NONE', relation: 'NONE', source: 'SYNTHETIC_PROVIDER' };
const base = {
  freshStateRead: true,
  currentStateResolved: true,
  targetIdentityVerified: true,
  necessityEstablished: true,
  mutating: true,
  activeWork: none,
  currentFingerprint: 'old',
  desiredFingerprint: 'new',
  validationPlanReady: true,
  selectedValidationEvidence: ['PROVIDER_READBACK'],
};

const matrix = [
  ['fresh-state gate', { freshStateRead: false }, PREFLIGHT_DECISIONS.BLOCKED_FRESH_STATE_REQUIRED],
  ['resolved-current gate', { currentStateResolved: false }, PREFLIGHT_DECISIONS.BLOCKED_CURRENT_RESOLUTION_REQUIRED],
  ['target-identity gate', { targetIdentityVerified: false }, PREFLIGHT_DECISIONS.BLOCKED_TARGET_IDENTITY_REQUIRED],
  ['active-work check gate', { activeWork: { checked: false } }, PREFLIGHT_DECISIONS.BLOCKED_ACTIVE_WORK_CHECK_REQUIRED],
  ['unknown visibility gate', { activeWork: { checked: true, status: 'UNKNOWN', relation: 'UNKNOWN', source: 'SYNTHETIC' } }, PREFLIGHT_DECISIONS.BLOCKED_ACTIVE_WORK_VISIBILITY_UNKNOWN],
  ['same objective active waits', { activeWork: { checked: true, status: 'ACTIVE', relation: 'SAME_OBJECTIVE', source: 'SYNTHETIC', runKey: 'A' } }, PREFLIGHT_DECISIONS.WAIT_ACTIVE_WORK_SAME_OBJECTIVE],
  ['same objective completed no-op', { activeWork: { checked: true, status: 'COMPLETED', relation: 'SAME_OBJECTIVE', outcomeVerified: true, source: 'SYNTHETIC', runKey: 'A' } }, PREFLIGHT_DECISIONS.NO_OP_ACTIVE_WORK_COMPLETED_SAME_OBJECTIVE],
  ['upstream active waits', { activeWork: { checked: true, status: 'ACTIVE', relation: 'UPSTREAM_DEPENDENCY', source: 'SYNTHETIC', runKey: 'A' } }, PREFLIGHT_DECISIONS.WAIT_ACTIVE_WORK_DEPENDENCY],
  ['same target overlap waits', { activeWork: { checked: true, status: 'ACTIVE', relation: 'SAME_TARGET_DIFFERENT_OBJECTIVE', expectedDeltaOverlap: true, source: 'SYNTHETIC', runKey: 'A' } }, PREFLIGHT_DECISIONS.WAIT_ACTIVE_WORK_TARGET_CONFLICT],
  ['unchanged blocker waits', { blockerKnown: true, blockerChanged: false }, PREFLIGHT_DECISIONS.WAIT_TRIGGER_UNCHANGED_BLOCKER],
  ['objective satisfied no-op', { objectiveSatisfied: true }, PREFLIGHT_DECISIONS.NO_OP_ALREADY_SATISFIED],
  ['identical fingerprint no-op', { currentFingerprint: 'same', desiredFingerprint: 'same' }, PREFLIGHT_DECISIONS.NO_OP_NO_MATERIAL_DELTA],
  ['missing validation plan blocks', { validationPlanReady: false, selectedValidationEvidence: [] }, PREFLIGHT_DECISIONS.BLOCKED_VALIDATION_PLAN_REQUIRED],
  ['independent active work permits material delta', { activeWork: { checked: true, status: 'ACTIVE', relation: 'INDEPENDENT', source: 'SYNTHETIC', runKey: 'A' } }, PREFLIGHT_DECISIONS.PROCEED],
];

let matrixCount = 0;
for (const provider of PREFLIGHT_PROVIDERS) {
  for (const [name, patch, expected] of matrix) {
    const actual = evaluateMaintenancePreflight({ ...base, provider, action: 'SYNTHETIC', ...patch });
    assert.equal(actual.decision, expected, `${provider}: ${name}`);
    assert.equal(actual.shouldExecute, expected === PREFLIGHT_DECISIONS.PROCEED, `${provider}: ${name}`);
    matrixCount += 1;
  }
}

// Controlled state-transition replays: no future natural use required.
const transitionReplay = [];
{
  const active = evaluateMaintenancePreflight({
    ...base,
    provider: 'DRIVE',
    formalMutation: true,
    activeWork: { checked: true, status: 'ACTIVE', relation: 'SAME_OBJECTIVE', source: 'FORMAL_LEASE', runKey: 'worker-A' },
  });
  assert.equal(active.decision, PREFLIGHT_DECISIONS.WAIT_ACTIVE_WORK_SAME_OBJECTIVE);
  const completed = evaluateMaintenancePreflight({
    ...base,
    provider: 'DRIVE',
    formalMutation: true,
    activeWork: { checked: true, status: 'COMPLETED', relation: 'SAME_OBJECTIVE', outcomeVerified: true, source: 'FORMAL_READBACK', runKey: 'worker-A' },
  });
  assert.equal(completed.decision, PREFLIGHT_DECISIONS.NO_OP_ACTIVE_WORK_COMPLETED_SAME_OBJECTIVE);
  transitionReplay.push('same-objective ACTIVE->WAIT->COMPLETED_VERIFIED->NO_OP');
}
{
  const unknown = evaluateMaintenancePreflight({
    ...base,
    provider: 'RENDER',
    activeWork: { checked: true, status: 'UNKNOWN', relation: 'UNKNOWN', source: 'UNRESOLVED' },
  });
  assert.equal(unknown.decision, PREFLIGHT_DECISIONS.BLOCKED_ACTIVE_WORK_VISIBILITY_UNKNOWN);
  const resolved = evaluateMaintenancePreflight({ ...base, provider: 'RENDER', activeWork: none });
  assert.equal(resolved.decision, PREFLIGHT_DECISIONS.PROCEED);
  transitionReplay.push('UNKNOWN_VISIBILITY->BLOCK->RESOLVED_NONE->PROCEED');
}
{
  const noPlan = evaluateMaintenancePreflight({
    ...base,
    provider: 'GITHUB',
    validationPlanReady: false,
    selectedValidationEvidence: [],
  });
  assert.equal(noPlan.decision, PREFLIGHT_DECISIONS.BLOCKED_VALIDATION_PLAN_REQUIRED);
  const planned = evaluateMaintenancePreflight({
    ...base,
    provider: 'GITHUB',
    validationPlanReady: true,
    selectedValidationEvidence: ['SYNTHETIC_OR_ADVERSARIAL_MATRIX', 'PROVIDER_READBACK'],
  });
  assert.equal(planned.decision, PREFLIGHT_DECISIONS.PROCEED);
  transitionReplay.push('NO_VALIDATION_PLAN->BLOCK->DECLARED_EVIDENCE->PROCEED');
}
{
  const conflict = evaluateMaintenancePreflight({
    ...base,
    provider: 'GITHUB',
    activeWork: { checked: true, status: 'ACTIVE', relation: 'SAME_TARGET_DIFFERENT_OBJECTIVE', expectedDeltaOverlap: true, source: 'GITHUB', runKey: 'worker-A' },
  });
  assert.equal(conflict.decision, PREFLIGHT_DECISIONS.WAIT_ACTIVE_WORK_TARGET_CONFLICT);
  const afterReadback = evaluateMaintenancePreflight({
    ...base,
    provider: 'GITHUB',
    activeWork: none,
    currentFingerprint: 'desired',
    desiredFingerprint: 'desired',
  });
  assert.equal(afterReadback.decision, PREFLIGHT_DECISIONS.NO_OP_NO_MATERIAL_DELTA);
  transitionReplay.push('TARGET_CONFLICT->WAIT->READBACK_SATISFIED->NO_OP');
}

// Property-style deterministic fuzzing. PROCEED must never escape required guards.
let seed = 0x5eed1234;
function rnd() {
  seed = (1664525 * seed + 1013904223) >>> 0;
  return seed / 0x100000000;
}
let fuzzCases = 0;
for (let i = 0; i < 1000; i += 1) {
  const freshStateRead = rnd() > 0.2;
  const currentStateResolved = rnd() > 0.2;
  const targetIdentityVerified = rnd() > 0.2;
  const necessityEstablished = rnd() > 0.2;
  const blockerKnown = rnd() > 0.75;
  const blockerChanged = rnd() > 0.5;
  const objectiveSatisfied = rnd() > 0.85;
  const sameFingerprint = rnd() > 0.75;
  const validationPlanReady = rnd() > 0.2;
  const awRoll = Math.floor(rnd() * 6);
  const activeVariants = [
    none,
    { checked: false },
    { checked: true, status: 'UNKNOWN', relation: 'UNKNOWN', source: 'FUZZ' },
    { checked: true, status: 'ACTIVE', relation: 'SAME_OBJECTIVE', source: 'FUZZ' },
    { checked: true, status: 'ACTIVE', relation: 'UPSTREAM_DEPENDENCY', source: 'FUZZ' },
    { checked: true, status: 'ACTIVE', relation: 'INDEPENDENT', source: 'FUZZ' },
  ];
  const input = {
    provider: PREFLIGHT_PROVIDERS[i % PREFLIGHT_PROVIDERS.length],
    action: 'FUZZ',
    mutating: true,
    freshStateRead,
    currentStateResolved,
    targetIdentityVerified,
    necessityEstablished,
    blockerKnown,
    blockerChanged,
    objectiveSatisfied,
    activeWork: activeVariants[awRoll],
    currentFingerprint: sameFingerprint ? 'same' : 'old',
    desiredFingerprint: sameFingerprint ? 'same' : 'new',
    validationPlanReady,
    selectedValidationEvidence: validationPlanReady ? ['PROVIDER_READBACK'] : [],
  };
  const actual = evaluateMaintenancePreflight(input);
  if (actual.shouldExecute) {
    assert.equal(freshStateRead, true, 'PROCEED escaped fresh-state gate');
    assert.equal(currentStateResolved, true, 'PROCEED escaped Current gate');
    assert.equal(targetIdentityVerified, true, 'PROCEED escaped target gate');
    assert.equal(input.activeWork.checked, true, 'PROCEED escaped active-work check');
    assert.notEqual(input.activeWork.status, 'UNKNOWN', 'PROCEED escaped unknown status');
    assert.notEqual(input.activeWork.relation, 'UNKNOWN', 'PROCEED escaped unknown relation');
    assert.equal(necessityEstablished, true, 'PROCEED escaped necessity gate');
    assert.equal(blockerKnown && !blockerChanged, false, 'PROCEED escaped unchanged blocker');
    assert.equal(objectiveSatisfied, false, 'PROCEED escaped satisfied objective');
    assert.equal(sameFingerprint, false, 'PROCEED escaped no-material-delta gate');
    assert.equal(validationPlanReady, true, 'PROCEED escaped validation-plan gate');
    assert.ok(input.selectedValidationEvidence.length >= 1, 'PROCEED escaped declared evidence requirement');
  }
  fuzzCases += 1;
}

// Mutation testing: deliberately remove critical guards and prove the oracle catches each defect.
const sourcePath = new URL('./maintenance-preflight.mjs', import.meta.url);
const source = await fs.readFile(sourcePath, 'utf8');
const tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), 'hao-preflight-mutants-'));
const mutations = [
  {
    name: 'remove resolved Current guard',
    from: "if (consequential && input.currentStateResolved !== true) {",
    to: 'if (false) {',
    input: { ...base, currentStateResolved: false },
    expected: PREFLIGHT_DECISIONS.BLOCKED_CURRENT_RESOLUTION_REQUIRED,
  },
  {
    name: 'remove active-work checked guard',
    from: 'if (activeWorkRequired && !activeWork.checked) {',
    to: 'if (false) {',
    input: { ...base, activeWork: { checked: false } },
    expected: PREFLIGHT_DECISIONS.BLOCKED_ACTIVE_WORK_CHECK_REQUIRED,
  },
  {
    name: 'remove same-objective duplicate guard',
    from: "if (activeWork.status === 'ACTIVE' && activeWork.relation === 'SAME_OBJECTIVE') {",
    to: 'if (false) {',
    input: { ...base, activeWork: { checked: true, status: 'ACTIVE', relation: 'SAME_OBJECTIVE', source: 'MUTANT' } },
    expected: PREFLIGHT_DECISIONS.WAIT_ACTIVE_WORK_SAME_OBJECTIVE,
  },
  {
    name: 'remove no-material-delta guard',
    from: 'if (currentFingerprint && desiredFingerprint && currentFingerprint === desiredFingerprint) {',
    to: 'if (false) {',
    input: { ...base, currentFingerprint: 'same', desiredFingerprint: 'same' },
    expected: PREFLIGHT_DECISIONS.NO_OP_NO_MATERIAL_DELTA,
  },
  {
    name: 'remove validation-plan guard',
    from: 'if (consequential && !validationPlan.ready) {',
    to: 'if (false) {',
    input: { ...base, validationPlanReady: false, selectedValidationEvidence: [] },
    expected: PREFLIGHT_DECISIONS.BLOCKED_VALIDATION_PLAN_REQUIRED,
  },
];
let mutantsKilled = 0;
for (let i = 0; i < mutations.length; i += 1) {
  const m = mutations[i];
  assert.ok(source.includes(m.from), `mutation anchor missing: ${m.name}`);
  const mutantSource = source.replace(m.from, m.to);
  const mutantPath = path.join(tmpDir, `mutant-${i}.mjs`);
  await fs.writeFile(mutantPath, mutantSource, 'utf8');
  const mod = await import(`${pathToFileURL(mutantPath).href}?v=${i}`);
  const actual = mod.evaluateMaintenancePreflight(m.input);
  assert.notEqual(actual.decision, m.expected, `mutation survived: ${m.name}`);
  mutantsKilled += 1;
}

console.log(JSON.stringify({
  event: 'hao_maintenance_preflight_adversarial_validation',
  result: 'PASS',
  providerMatrixCases: matrixCount,
  transitionReplay,
  deterministicFuzzCases: fuzzCases,
  mutantsKilled,
  mutantsTotal: mutations.length,
  naturalUseRequired: false,
}, null, 2));
