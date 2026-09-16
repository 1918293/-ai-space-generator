import { evaluateMaintenancePreflight, MAINTENANCE_PREFLIGHT_POLICY } from './maintenance-preflight.mjs';

export const LANES = Object.freeze({
  BLOCKED_LOCAL_DEVICE: 'BLOCKED_LOCAL_DEVICE',
  SINGLE_WRITE_GATEWAY: 'SINGLE_WRITE_GATEWAY',
  CHATGPT_PRIVATE_LANE: 'CHATGPT_PRIVATE_LANE',
  RENDER_READ_ONLY: 'RENDER_READ_ONLY',
  GITHUB_ACTIONS_PUBLIC: 'GITHUB_ACTIONS_PUBLIC',
  CHATGPT_NATIVE: 'CHATGPT_NATIVE',
});

export function routeTask(task = {}) {
  const reasons = [];
  const safeguards = [];

  if (task.maintenancePreflight && typeof task.maintenancePreflight === 'object') {
    const preflight = evaluateMaintenancePreflight({
      ...task.maintenancePreflight,
      formalMutation: task.formalMutation === true || task.maintenancePreflight.formalMutation === true,
    });
    if (!preflight.shouldExecute) {
      reasons.push(`Preflight stopped downstream execution: ${preflight.decision}.`);
      reasons.push(preflight.reason);
      safeguards.push('Treat NO_OP / WAIT_TRIGGER / BLOCKED as valid pre-execution outcomes; do not mutate downstream systems.');
      return { lane: null, disposition: preflight.decision, preflight, reasons, safeguards };
    }
  }

  if (task.requiresLocalDevice === true) {
    reasons.push('Task requires a user-owned local device, but Hao runtime is no-computer by default.');
    safeguards.push('Do not route to Desktop Commander or local daemon workflows.');
    return { lane: LANES.BLOCKED_LOCAL_DEVICE, reasons, safeguards };
  }

  if (task.formalMutation === true) {
    reasons.push('Task changes formal Hao System authority or another governed canonical target.');
    safeguards.push('Route through the existing Single Write Gateway only.');
    safeguards.push('Require target pre-read, dedupe/idempotency check, material-delta/necessity preflight, write, same-target readback, verify.');
    return { lane: LANES.SINGLE_WRITE_GATEWAY, reasons, safeguards };
  }

  const sensitive = task.privateData === true || task.personalData === true || task.containsSecrets === true;
  if (sensitive || task.connectedApps === true || task.longRunningBrowser === true) {
    reasons.push(sensitive
      ? 'Task contains private, personal, or secret-bearing data.'
      : 'Task benefits from connected apps or cloud browser continuity.');
    safeguards.push('Keep inputs out of public repositories and public Actions artifacts.');
    safeguards.push('Use ChatGPT private cloud execution / Work with Drive for private file round-trips.');
    return { lane: LANES.CHATGPT_PRIVATE_LANE, reasons, safeguards };
  }

  if (task.publicReadOnlyService === true) {
    reasons.push('Task is a public HTTPS, stateless, read-only control surface.');
    safeguards.push('No formal writes; Google Drive remains formal authority.');
    safeguards.push('Treat free-host cold start as an operational limitation.');
    return { lane: LANES.RENDER_READ_ONLY, reasons, safeguards };
  }

  if (task.deterministicCompute === true) {
    reasons.push('Task is non-sensitive deterministic CLI/Python processing suitable for an ephemeral runner.');
    safeguards.push('Use public GitHub Actions only for non-sensitive inputs and outputs.');
    safeguards.push('Pin behavior to commit identity and read back job status/logs/artifacts.');
    return { lane: LANES.GITHUB_ACTIONS_PUBLIC, reasons, safeguards };
  }

  reasons.push('Task does not require governed writes, private cloud execution, public service hosting, or external deterministic compute.');
  safeguards.push('Use native ChatGPT capabilities first; discover mature tools before adding infrastructure.');
  return { lane: LANES.CHATGPT_NATIVE, reasons, safeguards };
}

export const ROUTER_POLICY = Object.freeze({
  status: 'EXP_NON_AUTHORITY',
  deviceAssumption: 'NO_COMPUTER',
  formalAuthority: 'Google Drive',
  publicComputeRule: 'NON_SENSITIVE_ONLY',
  formalWriteRule: 'EXISTING_SINGLE_WRITE_GATEWAY_ONLY',
  maintenancePreflight: MAINTENANCE_PREFLIGHT_POLICY,
});
