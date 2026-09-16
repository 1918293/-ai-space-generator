export const LIVE_CURRENT_SCHEMA_VERSION = "0.1.0-exp";

const PRIVATE_ONLY_KEYS = new Set([
  "mode",
  "task",
  "project",
  "ownerEmail",
  "spreadsheetId",
  "documentId",
  "fileId",
  "rawNotes",
  "runKey",
  "leaseEpoch",
  "providerCredential",
  "accessToken",
  "refreshToken"
]);

function assertObject(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
}

export function validatePrivateLiveCurrent(current) {
  assertObject(current, "current");
  assertObject(current.conversation, "current.conversation");
  assertObject(current.formal, "current.formal");
  assertObject(current.freshness, "current.freshness");

  if (current.artifactRole !== "PRIVATE_LIVE_CURRENT") {
    throw new Error("artifactRole must be PRIVATE_LIVE_CURRENT");
  }
  if (current.formal.formalAuthority !== "Google Drive") {
    throw new Error("formalAuthority must remain Google Drive");
  }
  if (current.formal.sourceClass !== "FRESH_CONNECTED_AUTHORITY_READ") {
    throw new Error("formal source must be a fresh connected authority read");
  }
  if (!current.freshness.formalReadAt) {
    throw new Error("formalReadAt is required");
  }
  if (!current.conversation.mode || !current.conversation.task) {
    throw new Error("conversation mode and task are required in the private view");
  }
  return true;
}

export function toPublicSafeProjection(current) {
  validatePrivateLiveCurrent(current);

  return {
    schemaVersion: LIVE_CURRENT_SCHEMA_VERSION,
    artifactRole: "PUBLIC_SAFE_PROJECTION",
    formalAuthority: "Google Drive",
    projectionSource: "SANITIZED_DERIVED_FROM_PRIVATE_LIVE_CURRENT",
    system: {
      status: current.formal.systemStatus ?? "UNKNOWN",
      currentNavigation: current.formal.currentNavigation ?? null,
      counts: {
        pending: Number(current.formal.counts?.pending ?? 0),
        blockers: Number(current.formal.counts?.blockers ?? 0)
      }
    },
    runtime: {
      controlSurface: current.runtime?.controlSurface ?? "Render read-only MCP",
      version: current.runtime?.version ?? null,
      widgetFidelity: current.runtime?.widgetFidelity ?? null
    },
    freshness: {
      formalReadAt: current.freshness.formalReadAt,
      projectionBuiltAt: current.freshness.projectionBuiltAt ?? null
    },
    privacy: {
      privateFieldsRedacted: true,
      authorityCredentialsPresent: false,
      publicEndpointMayMutateAuthority: false
    }
  };
}

function walk(value, path = []) {
  if (Array.isArray(value)) {
    return value.flatMap((item, index) => walk(item, [...path, String(index)]));
  }
  if (value && typeof value === "object") {
    return Object.entries(value).flatMap(([key, child]) => [
      { key, value: child, path: [...path, key] },
      ...walk(child, [...path, key])
    ]);
  }
  return [];
}

export function assertPublicProjectionSafe(projection, forbiddenValues = []) {
  assertObject(projection, "projection");
  if (projection.artifactRole !== "PUBLIC_SAFE_PROJECTION") {
    throw new Error("public projection must declare PUBLIC_SAFE_PROJECTION");
  }
  if (projection.formalAuthority !== "Google Drive") {
    throw new Error("public projection cannot redefine formal authority");
  }
  if (projection.privacy?.authorityCredentialsPresent !== false) {
    throw new Error("public projection must not contain authority credentials");
  }
  if (projection.privacy?.publicEndpointMayMutateAuthority !== false) {
    throw new Error("public projection must remain non-mutating");
  }

  for (const entry of walk(projection)) {
    if (PRIVATE_ONLY_KEYS.has(entry.key)) {
      throw new Error(`private-only key leaked at ${entry.path.join(".")}`);
    }
  }

  const serialized = JSON.stringify(projection);
  for (const value of forbiddenValues.filter(Boolean)) {
    if (serialized.includes(String(value))) {
      throw new Error(`forbidden private value leaked: ${value}`);
    }
  }
  return true;
}

export const LIVE_CURRENT_BOUNDARY = Object.freeze({
  privateReader: "ChatGPT Google Drive connector now; future authenticated bridge allowed",
  publicRenderRole: "sanitized read-only projection only",
  publicRenderMayReadPrivateDriveDirectly: false,
  publicRenderMayStoreProviderCredentials: false,
  publicRenderMayMutateAuthority: false,
  formalAuthority: "Google Drive"
});
