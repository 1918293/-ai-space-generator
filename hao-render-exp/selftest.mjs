import { createHash } from "node:crypto";

const PORT = Number(process.env.PORT || 10000);
const BASE = `http://127.0.0.1:${PORT}`;
const EXPECTED_WIDGET_BYTES = 18232;
const EXPECTED_WIDGET_SHA256 = "6f96846097c3b6e119febca10e53b54c0996d78405c23b2f3795829d7f57a394";

async function post(payload) {
  const r = await fetch(`${BASE}/mcp`, {
    method: "POST",
    headers: { "content-type": "application/json", "accept": "application/json, text/event-stream" },
    body: JSON.stringify(payload)
  });
  const text = await r.text();
  return { status: r.status, body: text ? JSON.parse(text) : null };
}

function check(name, condition, detail = {}) {
  console.log(JSON.stringify({ event: "hao_mcp_selftest", name, pass: Boolean(condition), ...detail }));
  return Boolean(condition);
}

function utf8Bytes(text = "") {
  return Buffer.byteLength(text, "utf8");
}

function sha256(text = "") {
  return createHash("sha256").update(Buffer.from(text, "utf8")).digest("hex");
}

async function run() {
  const results = [];
  try {
    const health = await fetch(`${BASE}/healthz`);
    const hb = await health.json();
    results.push(check(
      "healthz",
      health.status === 200 &&
      hb.ok === true &&
      hb.formalAuthority === "Google Drive" &&
      hb.projectionFreshness === "STATIC_DEPLOYMENT_SNAPSHOT" &&
      hb.systemAdminScope === "PUBLIC_NON_AUTHORITY_STATIC" &&
      hb.maintenanceMutation === "DISABLED" &&
      hb.maintenancePreflight === "SHARED_PREFLIGHT_BEFORE_EXECUTION" &&
      hb.activeWorkAwareness === "REQUIRED_BEFORE_CONSEQUENTIAL_PREFLIGHT" &&
      hb.validationPlan === "REQUIRED_BEFORE_DECLARED_CONSEQUENTIAL_PROCEED" &&
      hb.admissionFailClosed === "MISSING_PREFLIGHT_BLOCKS_DECLARED_CONSEQUENTIAL" &&
      hb.taskRouter === "READ_ONLY" &&
      hb.widgetFidelity === "PASS" &&
      hb.widgetBytes === EXPECTED_WIDGET_BYTES &&
      hb.widgetSha256 === EXPECTED_WIDGET_SHA256,
      { status: health.status, version: hb.version, projectionFreshness: hb.projectionFreshness, systemAdminScope: hb.systemAdminScope, maintenanceMutation: hb.maintenanceMutation, validationPlan: hb.validationPlan, admissionFailClosed: hb.admissionFailClosed, widgetBytes: hb.widgetBytes, widgetSha256: hb.widgetSha256 }
    ));

    const init = await post({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "render-selftest", version: "0.5" } } });
    results.push(check("initialize", init.status === 200 && init.body?.result?.protocolVersion === "2025-06-18" && init.body?.result?.serverInfo?.name === "hao-system-control", { status: init.status }));

    const tools = await post({ jsonrpc: "2.0", id: 2, method: "tools/list", params: {} });
    const ts = tools.body?.result?.tools ?? [];
    const routeTool = ts.find(t => t.name === "route_hao_task");
    const routeProps = routeTool?.inputSchema?.properties ?? {};
    const preflightSchema = routeProps?.maintenancePreflight;
    const evidenceSchema = preflightSchema?.properties?.selectedValidationEvidence;
    results.push(check(
      "tools/list validation-plan admission schema",
      tools.status === 200 &&
      ts.length === 3 &&
      ts.every(t => t.annotations?.readOnlyHint === true) &&
      ts.some(t => t.name === "get_hao_system_snapshot") &&
      ts.some(t => t.name === "render_hao_system_control") &&
      routeProps?.mutating?.type === "boolean" &&
      routeProps?.consequential?.type === "boolean" &&
      preflightSchema?.type === "object" &&
      preflightSchema?.properties?.currentStateResolved?.type === "boolean" &&
      preflightSchema?.properties?.activeWork?.type === "object" &&
      preflightSchema?.properties?.validationPlanReady?.type === "boolean" &&
      evidenceSchema?.type === "array" &&
      evidenceSchema?.uniqueItems === true &&
      evidenceSchema?.items?.enum?.includes("PROVIDER_READBACK") &&
      evidenceSchema?.items?.enum?.includes("POST_RELEASE_STABILITY"),
      { status: tools.status, count: ts.length, evidenceTypes: evidenceSchema?.items?.enum?.length ?? 0 }
    ));

    const rl = await post({ jsonrpc: "2.0", id: 3, method: "resources/list", params: {} });
    const resources = rl.body?.result?.resources ?? [];
    results.push(check("resources/list", rl.status === 200 && resources[0]?.uri === "ui://widget/hao-system-control-v3.html" && resources[0]?.mimeType === "text/html;profile=mcp-app", { status: rl.status, count: resources.length }));

    const rr = await post({ jsonrpc: "2.0", id: 4, method: "resources/read", params: { uri: "ui://widget/hao-system-control-v3.html" } });
    const content = rr.body?.result?.contents?.[0];
    const widgetText = typeof content?.text === "string" ? content.text : "";
    const widgetBytes = utf8Bytes(widgetText);
    const widgetSha = sha256(widgetText);
    results.push(check(
      "resources/read exact widget",
      rr.status === 200 &&
      content?.uri === "ui://widget/hao-system-control-v3.html" &&
      content?.mimeType === "text/html;profile=mcp-app" &&
      widgetBytes === EXPECTED_WIDGET_BYTES &&
      widgetSha === EXPECTED_WIDGET_SHA256,
      { status: rr.status, widgetBytes, widgetSha256: widgetSha }
    ));

    const preview = await fetch(`${BASE}/widget-preview`);
    const previewText = await preview.text();
    const previewBytes = utf8Bytes(previewText);
    const previewSha = sha256(previewText);
    results.push(check(
      "widget-preview exact widget",
      preview.status === 200 && previewBytes === EXPECTED_WIDGET_BYTES && previewSha === EXPECTED_WIDGET_SHA256,
      { status: preview.status, widgetBytes: previewBytes, widgetSha256: previewSha }
    ));

    const snap = await post({ jsonrpc: "2.0", id: 5, method: "tools/call", params: { name: "get_hao_system_snapshot", arguments: {} } });
    const s = snap.body?.result?.structuredContent?.snapshot;
    const architecture = Array.isArray(s?.architecture) ? s.architecture : [];
    const admin = s?.systemAdmin ?? {};
    results.push(check(
      "tools/call snapshot",
      snap.status === 200 &&
      s?.artifactRole === "READ_ONLY_WORKING_PROJECTION" &&
      s?.formalAuthority === "Google Drive" &&
      s?.projectionFreshness === "STATIC_DEPLOYMENT_SNAPSHOT" &&
      admin.surfaceScope === "PUBLIC_NON_AUTHORITY_STATIC" &&
      admin.privateLifecycleCounts === "NOT_PUBLISHED" &&
      admin.currentActionableWorkload === "RESOLVED_CURRENT_REQUIRED" &&
      admin.runtimeHealth === "FRESH_PROVIDER_READ_REQUIRED" &&
      admin.ciHealth === "FRESH_PROVIDER_READ_REQUIRED" &&
      admin.maintenanceMutation === "DISABLED" &&
      admin.maintenancePreflight === "SHARED_PREFLIGHT_BEFORE_EXECUTION" &&
      admin.currentResolution === "REQUIRED_BEFORE_CONSEQUENTIAL_PREFLIGHT" &&
      admin.activeWorkAwareness === "REQUIRED_BEFORE_CONSEQUENTIAL_PREFLIGHT" &&
      admin.validationPlan === "REQUIRED_BEFORE_DECLARED_CONSEQUENTIAL_PROCEED" &&
      admin.admissionFailClosed === "MISSING_PREFLIGHT_BLOCKS_DECLARED_CONSEQUENTIAL" &&
      s?.routerPolicy?.deviceAssumption === "NO_COMPUTER" &&
      s?.routerPolicy?.consequentialPreflightRule === "REQUIRED_WHEN_FORMAL_MUTATION_OR_MUTATING_OR_CONSEQUENTIAL" &&
      s?.routerPolicy?.missingPreflightDisposition === "BLOCKED_MAINTENANCE_PREFLIGHT_REQUIRED" &&
      s?.routerPolicy?.maintenancePreflight?.noOpIsValidOutcome === true &&
      s?.routerPolicy?.maintenancePreflight?.waitIsValidOutcome === true &&
      s?.routerPolicy?.maintenancePreflight?.ruleOrder?.[3] === "ACTIVE_WORK_AWARENESS" &&
      s?.routerPolicy?.maintenancePreflight?.ruleOrder?.[8] === "VALIDATION_PLAN" &&
      s?.routerPolicy?.maintenancePreflight?.formalMutationBoundary === "SINGLE_WRITE_GATEWAY" &&
      typeof s?.purpose === "string" && s.purpose.length > 0 &&
      typeof s?.coreProblem === "string" && s.coreProblem.length > 0 &&
      architecture.length === 4 &&
      architecture.every(x => x?.level === 3 && typeof x?.label === "string" && typeof x?.role === "string") &&
      s?.widgetArtifact?.bytes === EXPECTED_WIDGET_BYTES &&
      s?.widgetArtifact?.sha256 === EXPECTED_WIDGET_SHA256 &&
      s?.widgetArtifact?.fidelity === "PASS",
      { status: snap.status, architectureCount: architecture.length, validationPlan: admin.validationPlan, admissionFailClosed: admin.admissionFailClosed }
    ));

    const render = await post({ jsonrpc: "2.0", id: 6, method: "tools/call", params: { name: "render_hao_system_control", arguments: {} } });
    results.push(check("tools/call render", render.status === 200 && render.body?.result?._meta?.["openai/outputTemplate"] === "ui://widget/hao-system-control-v3.html", { status: render.status }));

    const routePrivate = await post({ jsonrpc: "2.0", id: 7, method: "tools/call", params: { name: "route_hao_task", arguments: { deterministicCompute: true, privateData: true } } });
    const rp = routePrivate.body?.result?.structuredContent;
    results.push(check("tools/call route private", routePrivate.status === 200 && rp?.route?.lane === "CHATGPT_PRIVATE_LANE" && rp?.policy?.formalAuthority === "Google Drive", { status: routePrivate.status, lane: rp?.route?.lane }));

    const routeFormalMissing = await post({ jsonrpc: "2.0", id: 8, method: "tools/call", params: { name: "route_hao_task", arguments: { formalMutation: true, publicReadOnlyService: true } } });
    const rfm = routeFormalMissing.body?.result?.structuredContent?.route;
    results.push(check(
      "tools/call formal mutation missing preflight fails closed",
      routeFormalMissing.status === 200 && rfm?.lane === null && rfm?.disposition === "BLOCKED_MAINTENANCE_PREFLIGHT_REQUIRED",
      { status: routeFormalMissing.status, lane: rfm?.lane, disposition: rfm?.disposition }
    ));

    const none = { checked: true, status: "NONE", relation: "NONE", source: "PROVIDER_CURRENT" };
    const routeFormalReady = await post({ jsonrpc: "2.0", id: 9, method: "tools/call", params: { name: "route_hao_task", arguments: {
      formalMutation: true,
      publicReadOnlyService: true,
      maintenancePreflight: {
        provider: "DRIVE",
        action: "FORMAL_WRITE",
        freshStateRead: true,
        currentStateResolved: true,
        targetIdentityVerified: true,
        necessityEstablished: true,
        activeWork: none,
        currentFingerprint: "v1",
        desiredFingerprint: "v2",
        validationPlanReady: true,
        selectedValidationEvidence: ["PROVIDER_READBACK", "POST_RELEASE_STABILITY"]
      }
    } } });
    const rfr = routeFormalReady.body?.result?.structuredContent?.route;
    results.push(check(
      "tools/call formal mutation with preflight routes gateway",
      routeFormalReady.status === 200 && rfr?.lane === "SINGLE_WRITE_GATEWAY" && rfr?.preflight === undefined,
      { status: routeFormalReady.status, lane: rfr?.lane }
    ));

    const routeConsequentialMissing = await post({ jsonrpc: "2.0", id: 10, method: "tools/call", params: { name: "route_hao_task", arguments: { consequential: true, deterministicCompute: true } } });
    const rcm = routeConsequentialMissing.body?.result?.structuredContent?.route;
    results.push(check(
      "tools/call consequential compute missing preflight fails closed",
      routeConsequentialMissing.status === 200 && rcm?.lane === null && rcm?.disposition === "BLOCKED_MAINTENANCE_PREFLIGHT_REQUIRED",
      { status: routeConsequentialMissing.status, lane: rcm?.lane, disposition: rcm?.disposition }
    ));

    const routeConsequentialReady = await post({ jsonrpc: "2.0", id: 11, method: "tools/call", params: { name: "route_hao_task", arguments: {
      consequential: true,
      deterministicCompute: true,
      maintenancePreflight: {
        provider: "GITHUB",
        action: "RUN_COMPUTE",
        freshStateRead: true,
        currentStateResolved: true,
        targetIdentityVerified: true,
        necessityEstablished: true,
        activeWork: none,
        currentFingerprint: "old",
        desiredFingerprint: "new",
        validationPlanReady: true,
        selectedValidationEvidence: ["SYNTHETIC_OR_ADVERSARIAL_MATRIX", "PROVIDER_READBACK"]
      }
    } } });
    const rcr = routeConsequentialReady.body?.result?.structuredContent?.route;
    results.push(check(
      "tools/call consequential compute with preflight routes public compute",
      routeConsequentialReady.status === 200 && rcr?.lane === "GITHUB_ACTIONS_PUBLIC",
      { status: routeConsequentialReady.status, lane: rcr?.lane }
    ));

    const routeNoOp = await post({ jsonrpc: "2.0", id: 12, method: "tools/call", params: { name: "route_hao_task", arguments: {
      deterministicCompute: true,
      maintenancePreflight: {
        provider: "GITHUB",
        action: "UPDATE_FILE",
        freshStateRead: true,
        currentStateResolved: true,
        targetIdentityVerified: true,
        mutating: true,
        necessityEstablished: true,
        activeWork: none,
        currentFingerprint: "same",
        desiredFingerprint: "same"
      }
    } } });
    const rn = routeNoOp.body?.result?.structuredContent?.route;
    results.push(check(
      "tools/call maintenance no-op before validation-plan requirement",
      routeNoOp.status === 200 && rn?.lane === null && rn?.disposition === "NO_OP_NO_MATERIAL_DELTA" && rn?.preflight?.shouldExecute === false,
      { status: routeNoOp.status, disposition: rn?.disposition, lane: rn?.lane }
    ));

    const routeActive = await post({ jsonrpc: "2.0", id: 13, method: "tools/call", params: { name: "route_hao_task", arguments: {
      formalMutation: true,
      maintenancePreflight: {
        provider: "DRIVE",
        action: "FORMAL_WRITE",
        freshStateRead: true,
        currentStateResolved: true,
        targetIdentityVerified: true,
        necessityEstablished: true,
        activeWork: {
          checked: true,
          status: "ACTIVE",
          relation: "SAME_OBJECTIVE",
          targetConflict: true,
          expectedDeltaOverlap: true,
          source: "FORMAL_LEASE",
          runKey: "other-run"
        }
      }
    } } });
    const ra = routeActive.body?.result?.structuredContent?.route;
    results.push(check(
      "tools/call active same-objective waits before validation-plan requirement",
      routeActive.status === 200 && ra?.lane === null && ra?.disposition === "WAIT_ACTIVE_WORK_SAME_OBJECTIVE" && ra?.preflight?.shouldExecute === false,
      { status: routeActive.status, disposition: ra?.disposition, lane: ra?.lane }
    ));

    const notification = await fetch(`${BASE}/mcp`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized", params: {} })
    });
    results.push(check("notification 202", notification.status === 202, { status: notification.status }));

    console.log(JSON.stringify({ event: "hao_mcp_selftest_summary", passed: results.filter(Boolean).length, total: results.length, result: results.every(Boolean) ? "PASS" : "FAIL" }));
  } catch (error) {
    console.error(JSON.stringify({ event: "hao_mcp_selftest_summary", result: "ERROR", error: String(error?.stack || error) }));
  }
}

if (!process.execArgv.includes("--check")) setTimeout(run, 2000);
