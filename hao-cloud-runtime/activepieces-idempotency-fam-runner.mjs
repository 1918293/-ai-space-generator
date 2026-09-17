import fs from 'node:fs';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';

const BASE = 'http://127.0.0.1:8080/api/v1';
const MOCK_BASE = 'http://127.0.0.1:9091';
const CLOUD_PIECES = 'https://cloud.activepieces.com/api/v1/pieces';
const runId = process.env.GITHUB_RUN_ID_VALUE ?? 'local';
const syntheticCredential = process.env.FAM_CREDENTIAL;

const report = {
  schema: 'hao-activepieces-idempotency-fam-v1',
  authority: 'NONE',
  formalAuthorityMutation: false,
  productionCanonicalTargetTouched: false,
  mockTargetOnly: true,
  githubRunId: runId,
  phases: {},
};

fs.mkdirSync('activepieces-fam-output', { recursive: true });
const persist = () => fs.writeFileSync('activepieces-fam-output/idempotency-report.json', JSON.stringify(report, null, 2));
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
    throw new Error(`${method} ${path} -> ${response.status}: ${JSON.stringify(parsed).slice(0, 800)}`);
  }
  return parsed;
}

async function externalJson(url) {
  const response = await fetch(url, { headers: { accept: 'application/json' }, signal: AbortSignal.timeout(60_000) });
  const text = await response.text();
  if (!response.ok) throw new Error(`GET ${url} -> ${response.status}: ${text.slice(0, 500)}`);
  return JSON.parse(text);
}

function semverParts(version) {
  const match = String(version).match(/^(\d+)\.(\d+)\.(\d+)/);
  return match ? match.slice(1).map(Number) : [0, 0, 0];
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
  assert.equal(typeof version, 'string');
  return version.startsWith('~') ? version : `~${version}`;
}

async function waitTerminal({ credential, projectId, flowId, excludeRunId = null, timeoutSeconds = 120 }) {
  for (let i = 0; i < timeoutSeconds; i += 1) {
    const params = new URLSearchParams({ projectId, flowId, limit: '20' });
    const runs = listData(await api(`/flow-runs?${params.toString()}`, { credential }));
    const candidate = runs.find((run) => run?.id !== excludeRunId && run?.status && !['RUNNING', 'QUEUED', 'PAUSED'].includes(run.status));
    if (candidate) return candidate;
    await sleep(1000);
  }
  throw new Error(`no terminal run for flow ${flowId}`);
}

async function waitRunById({ credential, projectId, flowId, runId: targetRunId, timeoutSeconds = 120 }) {
  for (let i = 0; i < timeoutSeconds; i += 1) {
    const params = new URLSearchParams({ projectId, flowId, limit: '20' });
    const runs = listData(await api(`/flow-runs?${params.toString()}`, { credential }));
    const candidate = runs.find((run) => run?.id === targetRunId);
    if (candidate?.status && !['RUNNING', 'QUEUED', 'PAUSED'].includes(candidate.status)) return candidate;
    await sleep(1000);
  }
  throw new Error(`run ${targetRunId} did not become terminal`);
}

async function mockState(lane, actionId) {
  const url = new URL(`${MOCK_BASE}/state`);
  url.searchParams.set('lane', lane);
  url.searchParams.set('action_id', actionId);
  return externalJson(url);
}

async function triggerFlow(flowId) {
  const response = await fetch(`${BASE}/webhooks/${encodeURIComponent(flowId)}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ probe: 'hao-activepieces-idempotency-fam', runId }),
    signal: AbortSignal.timeout(60_000),
  });
  assert.ok(response.status >= 200 && response.status < 300, `webhook returned ${response.status}`);
}

try {
  assert.ok(syntheticCredential, 'FAM_CREDENTIAL missing');
  const mockHealth = await externalJson(`${MOCK_BASE}/health`);
  assert.equal(mockHealth.ok, true);
  report.phases.mockTargetHealth = 'PASS';

  const signupBody = {
    email: `hao-idempotency-fam-${runId}@example.test`,
    password: syntheticCredential,
    firstName: 'Hao',
    lastName: 'IdempotencyFAM',
    trackEvents: false,
    newsLetter: false,
  };
  let auth = await api('/authentication/sign-up', { method: 'POST', body: signupBody });
  let credential = auth?.token;
  let projectId = auth?.projectId;
  assert.ok(credential, 'sign-up did not return token');
  if (!projectId) {
    const platform = await api('/platforms', {
      method: 'POST', credential, body: { name: 'Hao Idempotency FAM' },
    });
    credential = platform?.token;
    projectId = platform?.projectId;
  }
  assert.ok(credential && projectId, 'project bootstrap failed');
  report.phases.authentication = 'PASS';

  const flags = await api('/flags', { credential });
  const release = flags?.CURRENT_VERSION;
  const edition = flags?.EDITION;
  assert.match(String(release ?? ''), /^\d+\.\d+\.\d+$/);
  assert.equal(typeof edition, 'string');
  report.providerRelease = release;
  report.providerEdition = edition;

  const registryUrl = new URL(`${CLOUD_PIECES}/registry`);
  registryUrl.searchParams.set('edition', edition);
  registryUrl.searchParams.set('release', release);
  const registry = listData(await externalJson(registryUrl));
  const webhookExact = latestRegistryVersion(registry, '@activepieces/piece-webhook');
  const httpExact = latestRegistryVersion(registry, '@activepieces/piece-http');
  assert.ok(webhookExact, 'webhook piece missing');
  assert.ok(httpExact, 'http piece missing');

  async function installPiece(pieceName, pieceVersion) {
    return api('/pieces', {
      method: 'POST', credential,
      body: { packageType: 'REGISTRY', scope: 'PLATFORM', pieceName, pieceVersion },
    });
  }

  await installPiece('@activepieces/piece-webhook', webhookExact);
  await installPiece('@activepieces/piece-http', httpExact);
  const webhookVersion = rangeVersion(webhookExact);
  const httpVersion = rangeVersion(httpExact);
  report.phases.pieceInstall = 'PASS';
  report.pieceVersions = { webhook: webhookVersion, http: httpVersion };

  async function buildFlow(lane, actionId, payloadHash) {
    const flow = await api(`/flows?projectId=${encodeURIComponent(projectId)}`, {
      method: 'POST', credential,
      body: { displayName: `Hao Idempotency ${lane} ${runId}`, projectId },
    });
    const flowId = flow?.id;
    assert.ok(flowId, 'flow id missing');

    await api(`/flows/${encodeURIComponent(flowId)}`, {
      method: 'POST', credential,
      body: {
        type: 'UPDATE_TRIGGER',
        request: {
          name: 'trigger', valid: true, displayName: 'Catch Webhook', type: 'PIECE_TRIGGER',
          lastUpdatedDate: new Date().toISOString(),
          settings: {
            pieceName: '@activepieces/piece-webhook', pieceVersion: webhookVersion,
            triggerName: 'catch_webhook', input: { authType: 'none' }, propertySettings: {},
          },
        },
      },
    });

    await api(`/flows/${encodeURIComponent(flowId)}`, {
      method: 'POST', credential,
      body: {
        type: 'ADD_ACTION',
        request: {
          parentStep: 'trigger',
          action: {
            name: 'step_1', skip: false, type: 'PIECE', valid: true,
            displayName: `Write ${lane} mock target`,
            settings: {
              input: {
                method: 'POST',
                url: `http://host.docker.internal:9091/${lane}/write`,
                headers: {}, queryParams: {}, authType: 'NONE',
                body_type: 'json',
                body: { data: { action_id: actionId, payload_hash: payloadHash } },
                response_is_binary: false, use_proxy: false,
                timeout: 10, followRedirects: false, failureMode: 'retry_none',
              },
              pieceName: '@activepieces/piece-http',
              actionName: 'send_request',
              pieceVersion: httpVersion,
              propertySettings: {},
              errorHandlingOptions: {},
            },
          },
        },
      },
    });

    await api(`/flows/${encodeURIComponent(flowId)}`, {
      method: 'POST', credential,
      body: { type: 'LOCK_AND_PUBLISH', request: {} },
    });

    let enabled = false;
    for (let i = 0; i < 60; i += 1) {
      const current = await api(`/flows/${encodeURIComponent(flowId)}`, { credential });
      if (current?.status === 'ENABLED') { enabled = true; break; }
      await sleep(1000);
    }
    assert.equal(enabled, true, `${lane} flow did not enable`);
    return flowId;
  }

  async function exerciseLane(lane) {
    const actionId = `FAM-${runId}-${lane}`;
    const payloadHash = crypto.createHash('sha256').update(`payload:${runId}:${lane}`).digest('hex');
    const flowId = await buildFlow(lane, actionId, payloadHash);

    await triggerFlow(flowId);
    const failedRun = await waitTerminal({ credential, projectId, flowId });
    assert.notEqual(failedRun.status, 'SUCCEEDED', `${lane} first lost-ack run unexpectedly succeeded`);

    const afterLostAck = await mockState(lane, actionId);
    assert.equal(afterLostAck.attempt_count, 1, `${lane} expected exactly one first attempt`);
    assert.equal(afterLostAck.logical_count, 1, `${lane} write must have committed before lost ack`);

    await api(`/flow-runs/${encodeURIComponent(failedRun.id)}/retry`, {
      method: 'POST', credential,
      body: { strategy: 'FROM_FAILED_STEP', projectId },
      allowed: [200],
    });
    const retriedRun = await waitRunById({ credential, projectId, flowId, runId: failedRun.id });
    assert.equal(retriedRun.status, 'SUCCEEDED', `${lane} FROM_FAILED_STEP did not succeed`);

    const afterRetry = await mockState(lane, actionId);
    assert.ok(afterRetry.attempt_count >= 2, `${lane} retry did not re-execute HTTP effect`);

    await triggerFlow(flowId);
    const replayRun = await waitTerminal({ credential, projectId, flowId, excludeRunId: failedRun.id });
    assert.equal(replayRun.status, 'SUCCEEDED', `${lane} identical action_id replay failed`);
    const afterReplay = await mockState(lane, actionId);

    return {
      flowId,
      actionId,
      payloadHash,
      firstRunStatus: failedRun.status,
      retryRunStatus: retriedRun.status,
      replayRunStatus: replayRun.status,
      afterLostAck,
      afterRetry,
      afterReplay,
    };
  }

  const protectedResult = await exerciseLane('protected');
  const unprotectedResult = await exerciseLane('unprotected');

  assert.equal(protectedResult.afterRetry.logical_count, 1, 'protected target duplicated after retry');
  assert.equal(protectedResult.afterReplay.logical_count, 1, 'protected target duplicated on same action_id replay');
  assert.ok(protectedResult.afterReplay.attempt_count >= 3, 'protected replay was not actually attempted');

  assert.equal(unprotectedResult.afterRetry.logical_count, 2, 'unprotected control did not duplicate on retry');
  assert.equal(unprotectedResult.afterReplay.logical_count, 3, 'unprotected control did not duplicate on replay');

  report.phases.lostAckCommitBeforeFailure = 'PASS';
  report.phases.fromFailedStepActuallyReexecutesEffect = 'PASS';
  report.phases.protectedSameActionIdRetry = 'PASS';
  report.phases.protectedSameActionIdReplay = 'PASS';
  report.phases.unprotectedDuplicateControl = 'PASS';
  report.protected = protectedResult;
  report.unprotected = unprotectedResult;
  report.finding = 'Activepieces FROM_FAILED_STEP re-executes a non-idempotent external HTTP effect. Exactly-once logical outcome requires target-side idempotency or an equivalent atomic gate; provider success alone is insufficient.';
  report.result = 'PASS';
  persist();

  console.log(JSON.stringify({
    event: 'hao_activepieces_idempotency_fam',
    result: report.result,
    providerRelease: report.providerRelease,
    protectedLogicalCountAfterRetry: protectedResult.afterRetry.logical_count,
    protectedLogicalCountAfterReplay: protectedResult.afterReplay.logical_count,
    unprotectedLogicalCountAfterRetry: unprotectedResult.afterRetry.logical_count,
    unprotectedLogicalCountAfterReplay: unprotectedResult.afterReplay.logical_count,
    authority: report.authority,
    productionCanonicalTargetTouched: report.productionCanonicalTargetTouched,
  }, null, 2));
} catch (error) {
  report.result = 'FAIL';
  report.error = String(error?.stack || error?.message || error).slice(0, 3000);
  persist();
  throw error;
}
