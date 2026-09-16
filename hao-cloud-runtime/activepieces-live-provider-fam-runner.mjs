import fs from 'node:fs';
import assert from 'node:assert/strict';
import { buildPreflightFromActivepieces, normalizeActivepiecesRun } from './activepieces-active-work.mjs';

const BASE = 'http://127.0.0.1:8080/api/v1';
const CLOUD_PIECES = 'https://cloud.activepieces.com/api/v1/pieces';
const runId = process.env.GITHUB_RUN_ID_VALUE ?? 'local';
const syntheticEmail = `hao-live-fam-${runId}@example.test`;
const syntheticCredential = process.env.FAM_CREDENTIAL;
const tags = {
  runKey: `hao:run-key=LIVE-FAM-${runId}`,
  objective: 'hao:objective=ACTIVEPIECES_LIVE_FAM',
  target: 'hao:target=SYNTHETIC_TARGET',
  lane: 'hao:lane=GITHUB_ACTIONS_EPHEMERAL',
};
const expectedTags = Object.values(tags);
const report = {
  schema: 'hao-activepieces-live-provider-fam-v2',
  providerRole: 'ACTIVE_WORK_SIGNAL_ONLY',
  authority: 'NONE',
  formalAuthorityMutation: false,
  globalTaskDatabaseCreated: false,
  ephemeralProvider: true,
  imageDigest: process.env.IMAGE_DIGEST ?? null,
  githubRunId: runId,
  phases: {},
};

fs.mkdirSync('activepieces-fam-output', { recursive: true });
const persist = () => fs.writeFileSync('activepieces-fam-output/report.json', JSON.stringify(report, null, 2));
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const listData = (value) => Array.isArray(value) ? value : (value?.data ?? value?.items ?? []);

async function api(path, { method = 'GET', credential, body, allowed = [200, 201, 204] } = {}) {
  const headers = { accept: 'application/json' };
  if (body !== undefined) headers['content-type'] = 'application/json';
  if (credential) headers.authorization = `Bearer ${credential}`;
  const response = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(60_000),
  });
  const text = await response.text();
  let parsed = null;
  if (text) {
    try { parsed = JSON.parse(text); }
    catch { parsed = text; }
  }
  if (!allowed.includes(response.status)) {
    const safe = typeof parsed === 'string' ? parsed.slice(0, 500) : parsed;
    throw new Error(`${method} ${path} -> ${response.status}: ${JSON.stringify(safe)}`);
  }
  return parsed;
}

async function externalJson(url) {
  const response = await fetch(url, { headers: { accept: 'application/json' }, signal: AbortSignal.timeout(60_000) });
  const text = await response.text();
  if (!response.ok) throw new Error(`GET ${url} -> ${response.status}: ${text.slice(0, 300)}`);
  return JSON.parse(text);
}

function semverParts(version) {
  const match = String(version).match(/^(\d+)\.(\d+)\.(\d+)/);
  if (!match) return [0, 0, 0];
  return match.slice(1).map(Number);
}

function latestRegistryVersion(registry, pieceName) {
  const matches = registry.filter((entry) => entry?.name === pieceName && /^\d+\.\d+\.\d+/.test(String(entry?.version ?? '')));
  matches.sort((a, b) => {
    const av = semverParts(a.version);
    const bv = semverParts(b.version);
    for (let i = 0; i < 3; i += 1) if (av[i] !== bv[i]) return bv[i] - av[i];
    return 0;
  });
  return matches[0]?.version ?? null;
}

function rangeVersion(version) {
  assert.equal(typeof version, 'string', 'piece version missing');
  return version.startsWith('~') ? version : `~${version}`;
}

try {
  const signupBody = {
    email: syntheticEmail,
    password: syntheticCredential,
    firstName: 'Hao',
    lastName: 'LiveFAM',
    trackEvents: false,
    newsLetter: false,
  };
  let auth = await api('/authentication/sign-up', { method: 'POST', body: signupBody });
  let credential = auth?.token;
  let projectId = auth?.projectId;
  assert.ok(credential, 'sign-up did not return a token');
  if (!projectId) {
    const platform = await api('/platforms', {
      method: 'POST', credential, body: { name: 'Hao Live FAM' },
    });
    credential = platform?.token;
    projectId = platform?.projectId;
    assert.ok(credential && projectId, 'platform onboarding did not return user token/projectId');
  }
  report.phases.authentication = 'PASS';
  report.projectId = projectId;
  persist();

  const flags = await api('/flags', { credential });
  const release = flags?.CURRENT_VERSION;
  const edition = flags?.EDITION;
  assert.match(String(release ?? ''), /^\d+\.\d+\.\d+$/, 'CURRENT_VERSION flag missing or invalid');
  assert.equal(typeof edition, 'string', 'EDITION flag missing');
  report.providerRelease = release;
  report.providerEdition = edition;

  const registryUrl = new URL(`${CLOUD_PIECES}/registry`);
  registryUrl.searchParams.set('edition', edition);
  registryUrl.searchParams.set('release', release);
  const registry = listData(await externalJson(registryUrl));
  const exactWebhookVersion = latestRegistryVersion(registry, '@activepieces/piece-webhook');
  const exactTagsVersion = latestRegistryVersion(registry, '@activepieces/piece-tags');
  assert.ok(exactWebhookVersion, 'compatible webhook piece missing from cloud registry');
  assert.ok(exactTagsVersion, 'compatible tags piece missing from cloud registry');
  report.phases.piecePlan = 'PASS';
  report.piecePlan = { webhook: exactWebhookVersion, tags: exactTagsVersion, registryEntries: registry.length };
  persist();

  async function installPiece(pieceName, pieceVersion) {
    return api('/pieces', {
      method: 'POST',
      credential,
      body: { packageType: 'REGISTRY', scope: 'PLATFORM', pieceName, pieceVersion },
    });
  }

  const installedWebhook = await installPiece('@activepieces/piece-webhook', exactWebhookVersion);
  const installedTags = await installPiece('@activepieces/piece-tags', exactTagsVersion);
  assert.equal(installedWebhook?.name, '@activepieces/piece-webhook');
  assert.equal(installedTags?.name, '@activepieces/piece-tags');

  const localPieces = listData(await api(`/pieces?projectId=${encodeURIComponent(projectId)}&includeHidden=true`, { credential }));
  const webhookMeta = localPieces.find((piece) => piece?.name === '@activepieces/piece-webhook' && piece?.version === exactWebhookVersion);
  const tagsMeta = localPieces.find((piece) => piece?.name === '@activepieces/piece-tags' && piece?.version === exactTagsVersion);
  assert.ok(webhookMeta, 'installed webhook piece missing from local provider readback');
  assert.ok(tagsMeta, 'installed tags piece missing from local provider readback');
  const webhookVersion = rangeVersion(exactWebhookVersion);
  const tagsVersion = rangeVersion(exactTagsVersion);
  report.phases.pieceInstallReadback = 'PASS';
  report.pieceVersions = { webhook: webhookVersion, tags: tagsVersion };
  persist();

  const flow = await api(`/flows?projectId=${encodeURIComponent(projectId)}`, {
    method: 'POST', credential,
    body: { displayName: `Hao Live FAM ${runId}`, projectId },
  });
  const flowId = flow?.id;
  assert.ok(flowId, 'flow create did not return id');
  report.flowId = flowId;

  await api(`/flows/${encodeURIComponent(flowId)}`, {
    method: 'POST', credential,
    body: {
      type: 'UPDATE_TRIGGER',
      request: {
        name: 'trigger',
        valid: true,
        displayName: 'Catch Webhook',
        type: 'PIECE_TRIGGER',
        lastUpdatedDate: new Date().toISOString(),
        settings: {
          pieceName: '@activepieces/piece-webhook',
          pieceVersion: webhookVersion,
          triggerName: 'catch_webhook',
          input: { authType: 'none' },
          propertySettings: {},
        },
      },
    },
  });

  const actionNode = (name, displayName, tag) => ({
    name,
    skip: false,
    type: 'PIECE',
    valid: true,
    settings: {
      input: { name: tag },
      pieceName: '@activepieces/piece-tags',
      actionName: 'add_tag',
      pieceVersion: tagsVersion,
      propertySettings: {},
      errorHandlingOptions: {},
    },
    displayName,
  });

  let parentStep = 'trigger';
  for (const [name, label, tag] of [
    ['step_1', 'Tag RUN_KEY', tags.runKey],
    ['step_2', 'Tag Objective', tags.objective],
    ['step_3', 'Tag Target', tags.target],
    ['step_4', 'Tag Lane', tags.lane],
  ]) {
    await api(`/flows/${encodeURIComponent(flowId)}`, {
      method: 'POST', credential,
      body: { type: 'ADD_ACTION', request: { parentStep, action: actionNode(name, label, tag) } },
    });
    parentStep = name;
  }

  await api(`/flows/${encodeURIComponent(flowId)}`, {
    method: 'POST', credential, body: { type: 'LOCK_AND_PUBLISH', request: {} },
  });

  let enabled = false;
  for (let i = 0; i < 60; i += 1) {
    const current = await api(`/flows/${encodeURIComponent(flowId)}`, { credential });
    if (current?.status === 'ENABLED') { enabled = true; break; }
    await sleep(1000);
  }
  assert.equal(enabled, true, 'flow did not become ENABLED');
  report.phases.flowBuildPublish = 'PASS';
  persist();

  const webhookResponse = await fetch(`${BASE}/webhooks/${encodeURIComponent(flowId)}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ probe: 'hao-activepieces-live-fam', runId }),
    signal: AbortSignal.timeout(60_000),
  });
  assert.ok(webhookResponse.status >= 200 && webhookResponse.status < 300, `webhook returned ${webhookResponse.status}`);
  report.webhookHttpStatus = webhookResponse.status;

  let providerRun = null;
  for (let i = 0; i < 120; i += 1) {
    const params = new URLSearchParams({ projectId, flowId, limit: '20' });
    const runs = listData(await api(`/flow-runs?${params.toString()}`, { credential }));
    if (runs.length > 0) {
      const candidate = runs.find((run) => run?.tags?.includes(tags.runKey)) ?? runs[0];
      if (candidate?.status && !['RUNNING', 'QUEUED', 'PAUSED'].includes(candidate.status)) {
        providerRun = candidate;
        break;
      }
    }
    await sleep(1000);
  }
  assert.ok(providerRun, 'no terminal flow run observed');
  assert.equal(providerRun.status, 'SUCCEEDED', `flow run status=${providerRun.status}`);
  for (const tag of expectedTags) assert.ok(providerRun.tags?.includes(tag), `missing provider tag ${tag}`);
  report.providerRun = {
    id: providerRun.id,
    projectId: providerRun.projectId,
    flowId: providerRun.flowId,
    status: providerRun.status,
    tags: providerRun.tags,
    created: providerRun.created,
    updated: providerRun.updated,
    startTime: providerRun.startTime,
    finishTime: providerRun.finishTime,
  };
  report.phases.realFlowRun = 'PASS';
  persist();

  const filterParams = new URLSearchParams({ projectId, flowId, limit: '20' });
  filterParams.append('tags', tags.runKey);
  const filteredRuns = listData(await api(`/flow-runs?${filterParams.toString()}`, { credential }));
  assert.ok(filteredRuns.some((run) => run?.id === providerRun.id), 'server-side tag-filtered list did not return the run');
  assert.ok(filteredRuns.every((run) => run?.tags?.includes(tags.runKey)), 'tag-filter response contained a non-matching run');
  report.phases.serverSideTagFilter = 'PASS';
  report.filteredRunCount = filteredRuns.length;

  const candidate = {
    runKey: `LIVE-FAM-${runId}`,
    objective: 'ACTIVEPIECES_LIVE_FAM',
    target: 'SYNTHETIC_TARGET',
  };
  const normalizedWithoutReadback = normalizeActivepiecesRun(providerRun, candidate, { nowMs: Date.now() });
  assert.equal(normalizedWithoutReadback.status, 'UNKNOWN');
  assert.equal(normalizedWithoutReadback.relation, 'SAME_OBJECTIVE');
  const basePreflight = {
    action: 'LIVE_PROVIDER_FAM',
    freshStateRead: true,
    currentStateResolved: true,
    targetIdentityVerified: true,
    consequential: true,
    necessityEstablished: true,
    currentFingerprint: 'v1',
    desiredFingerprint: 'v2',
    validationPlanReady: true,
    selectedValidationEvidence: ['PROVIDER_READBACK'],
  };
  const failClosed = buildPreflightFromActivepieces({ runs: [providerRun], candidate, preflight: basePreflight });
  assert.equal(failClosed.decision, 'BLOCKED_ACTIVE_WORK_VISIBILITY_UNKNOWN');
  const withReadback = buildPreflightFromActivepieces({
    runs: [providerRun],
    candidate: { ...candidate, targetReadbackVerifiedSatisfied: true },
    preflight: basePreflight,
  });
  assert.equal(withReadback.decision, 'NO_OP_ACTIVE_WORK_COMPLETED_SAME_OBJECTIVE');
  report.phases.haoAdapter = {
    providerSuccessWithoutReadback: failClosed.decision,
    withIndependentSyntheticReadback: withReadback.decision,
    note: 'Second assertion validates adapter integration only; no governed target side effect is claimed by this tag-only flow.',
  };
  report.result = 'PASS';
  persist();
  console.log(JSON.stringify({
    event: 'hao_activepieces_live_provider_fam',
    result: report.result,
    imageDigest: report.imageDigest,
    providerRelease: report.providerRelease,
    flowRunStatus: report.providerRun.status,
    providerTags: report.providerRun.tags.length,
    serverSideTagFilter: report.phases.serverSideTagFilter,
    providerSuccessWithoutReadback: report.phases.haoAdapter.providerSuccessWithoutReadback,
    independentReadbackAdapterDecision: report.phases.haoAdapter.withIndependentSyntheticReadback,
    authority: report.authority,
    formalAuthorityMutation: report.formalAuthorityMutation,
  }, null, 2));
} catch (error) {
  report.result = 'FAIL';
  report.error = String(error?.message ?? error).slice(0, 1000);
  persist();
  throw error;
}
