import assert from "node:assert/strict";
import {
  LIVE_CURRENT_BOUNDARY,
  LIVE_CURRENT_SCHEMA_VERSION,
  assertPublicProjectionSafe,
  toPublicSafeProjection,
  validatePrivateLiveCurrent
} from "./live-current-contract.mjs";

const privateCurrent = {
  artifactRole: "PRIVATE_LIVE_CURRENT",
  conversation: {
    mode: "EXP",
    task: "SECRET_TASK_X",
    project: "PRIVATE_PROJECT_Y"
  },
  formal: {
    formalAuthority: "Google Drive",
    sourceClass: "FRESH_CONNECTED_AUTHORITY_READ",
    systemStatus: "ACTIVE",
    currentNavigation: "Master Index v1.0.2",
    counts: { pending: 2, blockers: 6 },
    spreadsheetId: "SHEET_PRIVATE_123",
    fileId: "FILE_PRIVATE_456",
    rawNotes: "RAW_PRIVATE_NOTES",
    runKey: "RUN_999",
    leaseEpoch: 42,
    ownerEmail: "owner@example.invalid"
  },
  runtime: {
    controlSurface: "Render read-only MCP",
    version: "0.4.2-widget-schema-exp",
    widgetFidelity: "PASS"
  },
  freshness: {
    formalReadAt: "2026-09-16T22:30:00+08:00",
    projectionBuiltAt: "2026-09-16T22:30:01+08:00"
  },
  providerCredential: "DO_NOT_LEAK_TOKEN"
};

assert.equal(validatePrivateLiveCurrent(privateCurrent), true);

const publicProjection = toPublicSafeProjection(privateCurrent);
assert.equal(publicProjection.schemaVersion, LIVE_CURRENT_SCHEMA_VERSION);
assert.equal(publicProjection.artifactRole, "PUBLIC_SAFE_PROJECTION");
assert.equal(publicProjection.formalAuthority, "Google Drive");
assert.equal(publicProjection.system.status, "ACTIVE");
assert.equal(publicProjection.system.counts.pending, 2);
assert.equal(publicProjection.system.counts.blockers, 6);
assert.equal(publicProjection.runtime.widgetFidelity, "PASS");
assert.equal(publicProjection.privacy.privateFieldsRedacted, true);
assert.equal(publicProjection.privacy.authorityCredentialsPresent, false);
assert.equal(publicProjection.privacy.publicEndpointMayMutateAuthority, false);

assert.equal(
  assertPublicProjectionSafe(publicProjection, [
    "SECRET_TASK_X",
    "PRIVATE_PROJECT_Y",
    "SHEET_PRIVATE_123",
    "FILE_PRIVATE_456",
    "RAW_PRIVATE_NOTES",
    "RUN_999",
    "owner@example.invalid",
    "DO_NOT_LEAK_TOKEN"
  ]),
  true
);

assert.throws(
  () => validatePrivateLiveCurrent({ ...privateCurrent, formal: { ...privateCurrent.formal, formalAuthority: "Render" } }),
  /formalAuthority/
);
assert.equal(LIVE_CURRENT_BOUNDARY.publicRenderMayReadPrivateDriveDirectly, false);
assert.equal(LIVE_CURRENT_BOUNDARY.publicRenderMayStoreProviderCredentials, false);
assert.equal(LIVE_CURRENT_BOUNDARY.publicRenderMayMutateAuthority, false);

console.log(JSON.stringify({
  event: "hao_live_current_contract_test",
  result: "PASS",
  schemaVersion: LIVE_CURRENT_SCHEMA_VERSION,
  publicKeys: Object.keys(publicProjection),
  privateLeakCheck: "PASS",
  formalAuthority: publicProjection.formalAuthority,
  publicRenderMayMutateAuthority: LIVE_CURRENT_BOUNDARY.publicRenderMayMutateAuthority
}));
