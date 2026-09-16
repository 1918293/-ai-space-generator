import http from "node:http";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { routeTask, ROUTER_POLICY } from "../hao-cloud-runtime/task-router.mjs";

const PORT = Number(process.env.PORT || 10000);
const HOST = "0.0.0.0";
const RESOURCE_URI = "ui://widget/hao-system-control-v3.html";
const RESOURCE_MIME = "text/html;profile=mcp-app";
const VERSION = "0.4.6-validation-plan-admission-exp";
const RELEASE_COMMIT = process.env.RENDER_GIT_COMMIT || "unknown";
const EXPECTED_WIDGET_BYTES = 18232;
const EXPECTED_WIDGET_SHA256 = "6f96846097c3b6e119febca10e53b54c0996d78405c23b2f3795829d7f57a394";
const WIDGET = readFileSync(new URL("./hao-system-widget.html", import.meta.url), "utf8");
const WIDGET_BYTES = Buffer.byteLength(WIDGET, "utf8");
const WIDGET_SHA256 = createHash("sha256").update(Buffer.from(WIDGET, "utf8")).digest("hex");

if (WIDGET_BYTES !== EXPECTED_WIDGET_BYTES || WIDGET_SHA256 !== EXPECTED_WIDGET_SHA256) {
  throw new Error(`Widget fidelity gate failed: bytes=${WIDGET_BYTES} sha256=${WIDGET_SHA256}`);
}

const SNAPSHOT = {
  artifactRole: "READ_ONLY_WORKING_PROJECTION",
  formalAuthority: "Google Drive",
  lifecycle: "EXP",
  owner: "Hao",
  wip: 0,
  projectionFreshness: "STATIC_DEPLOYMENT_SNAPSHOT",
  releaseVersion: VERSION,
  releaseCommit: RELEASE_COMMIT,
  purpose: "提供 Hao System 的手機優先、唯讀控制面：查看部署、路由與系統邊界，不直接修改正式 Authority。",
  coreProblem: "把 ChatGPT、雲端執行與正式 Authority 分離，讓 Auto 能選擇正確 execution lane，同時避免 stale Current、重工、未定義驗證方式與 public MCP 繞過正式寫入控制。",
  currentFocus: "No-computer Hao Control Surface：Shared Maintenance Preflight 已連接 Resolved Current、Active Work Awareness、pre-execution Validation Plan 與 declared consequential fail-closed admission。",
  nextAction: "只有在 fresh Current、target identity、active-work relation、material delta、necessity 與 validation plan 都充分時，才為 declared consequential action 選擇 execution lane。",
  desiredOutcome: "Hao 只需提出目標；系統先辨識已完成、進行中、等待依賴、真正需要執行與如何驗證，再選 execution lane。",
  doNotBuild: "不把 public Render 變成正式 Authority、不加入任意寫入、不公開私人 Drive 資料、不建立第二套 Gateway／工作資料庫／Agent runtime。",
  systemAdmin: {
    surfaceScope: "PUBLIC_NON_AUTHORITY_STATIC",
    privateLifecycleCounts: "NOT_PUBLISHED",
    currentActionableWorkload: "RESOLVED_CURRENT_REQUIRED",
    runtimeHealth: "FRESH_PROVIDER_READ_REQUIRED",
    ciHealth: "FRESH_PROVIDER_READ_REQUIRED",
    maintenanceMutation: "DISABLED",
    maintenancePreflight: "SHARED_PREFLIGHT_BEFORE_EXECUTION",
    currentResolution: "REQUIRED_BEFORE_CONSEQUENTIAL_PREFLIGHT",
    activeWorkAwareness: "REQUIRED_BEFORE_CONSEQUENTIAL_PREFLIGHT",
    validationPlan: "REQUIRED_BEFORE_DECLARED_CONSEQUENTIAL_PROCEED",
    admissionFailClosed: "MISSING_PREFLIGHT_BLOCKS_DECLARED_CONSEQUENTIAL"
  },
  architecture: [
    { level: 3, label: "Private Lane", role: "ChatGPT / Work + Google Drive；私人資料與 connected-app 工作。" },
    { level: 3, label: "Compute Lane", role: "GitHub Actions；非敏感 deterministic compute、QA、build。" },
    { level: 3, label: "Control Surface", role: "Render Free；公開 read-only MCP、Widget、health、routing projection。" },
    { level: 3, label: "Formal Mutation", role: "既有 Single Write Gateway only；CAS / dedup / readback / release。" }
  ],
  sources: [
    "Google Drive — Formal Authority（此 public EXP service 不直接讀取私人內容）",
    "GitHub EXP branch — code / CI / release identity",
    "Render — deployed runtime / logs / health evidence"
  ],
  routerPolicy: ROUTER_POLICY,
  widgetArtifact: {
    bytes: WIDGET_BYTES,
    sha256: WIDGET_SHA256,
    expectedBytes: EXPECTED_WIDGET_BYTES,
    expectedSha256: EXPECTED_WIDGET_SHA256,
    fidelity: "PASS"
  }
};

const ACTIVE_WORK_SCHEMA = {
  type: "object",
  properties: {
    checked: { type: "boolean" },
    status: { type: "string", enum: ["NONE", "ACTIVE", "COMPLETED", "BLOCKED", "UNKNOWN"] },
    relation: { type: "string", enum: ["NONE", "SAME_OBJECTIVE", "SAME_TARGET_DIFFERENT_OBJECTIVE", "UPSTREAM_DEPENDENCY", "INDEPENDENT", "UNKNOWN"] },
    targetConflict: { type: "boolean" },
    expectedDeltaOverlap: { type: "boolean" },
    outcomeVerified: { type: "boolean" },
    source: { type: "string" },
    runKey: { type: "string" }
  },
  additionalProperties: false
};

const VALIDATION_EVIDENCE_ENUM = [
  "REGRESSION_OR_HISTORY_REPLAY",
  "SYNTHETIC_OR_ADVERSARIAL_MATRIX",
  "DETERMINISTIC_PROPERTY_OR_FUZZ",
  "MUTATION_TESTING",
  "FAULT_INJECTION",
  "CONCURRENCY_OR_STATE_TRANSITION_REPLAY",
  "PROVIDER_READBACK",
  "POST_RELEASE_STABILITY"
];

const PREFLIGHT_SCHEMA = {
  type: "object",
  properties: {
    provider: { type: "string", enum: ["GITHUB", "RENDER", "DRIVE", "APPS_SCRIPT", "IMAGE_PIPELINE", "GENERIC"] },
    action: { type: "string" },
    freshStateRead: { type: "boolean" },
    currentStateResolved: { type: "boolean" },
    targetIdentityVerified: { type: "boolean" },
    activeWorkRequired: { type: "boolean" },
    activeWork: ACTIVE_WORK_SCHEMA,
    mutating: { type: "boolean" },
    consequential: { type: "boolean" },
    formalMutation: { type: "boolean" },
    blockerKnown: { type: "boolean" },
    blockerChanged: { type: "boolean" },
    objectiveSatisfied: { type: "boolean" },
    necessityEstablished: { type: "boolean" },
    currentFingerprint: { type: "string" },
    desiredFingerprint: { type: "string" },
    validationPlanReady: { type: "boolean" },
    selectedValidationEvidence: {
      type: "array",
      items: { type: "string", enum: VALIDATION_EVIDENCE_ENUM },
      uniqueItems: true
    }
  },
  additionalProperties: false
};

const ROUTE_PROPERTIES = {
  requiresLocalDevice: { type: "boolean" },
  formalMutation: { type: "boolean" },
  mutating: { type: "boolean" },
  consequential: { type: "boolean" },
  privateData: { type: "boolean" },
  personalData: { type: "boolean" },
  containsSecrets: { type: "boolean" },
  connectedApps: { type: "boolean" },
  longRunningBrowser: { type: "boolean" },
  publicReadOnlyService: { type: "boolean" },
  deterministicCompute: { type: "boolean" },
  maintenancePreflight: PREFLIGHT_SCHEMA
};

const TOOLS = [
  {
    name: "get_hao_system_snapshot",
    description: "Read the Hao System working projection only. No formal writes.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true }
  },
  {
    name: "render_hao_system_control",
    description: "Render the Hao System read-only control widget.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true },
    _meta: { "openai/outputTemplate": RESOURCE_URI }
  },
  {
    name: "route_hao_task",
    description: "Classify a task into the Hao no-computer execution lane. Tasks declared formalMutation, mutating, or consequential must include resolved maintenance preflight evidence and a validation plan or routing fails closed. Read-only classification only; this tool never performs the routed action.",
    inputSchema: { type: "object", properties: ROUTE_PROPERTIES, additionalProperties: false },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true }
  }
];

function cors(res) {
  res.setHeader("access-control-allow-origin", "*");
  res.setHeader("access-control-allow-methods", "GET,POST,OPTIONS");
  res.setHeader("access-control-allow-headers", "content-type,accept,mcp-protocol-version");
}

function sendJson(res, status, body) {
  cors(res);
  res.statusCode = status;
  res.setHeader("content-type", "application/json; charset=utf-8");
  res.end(JSON.stringify(body));
}

function rpc(res, id, result) {
  sendJson(res, 200, { jsonrpc: "2.0", id, result });
}

function rpcError(res, id, code, message) {
  sendJson(res, 200, { jsonrpc: "2.0", id: id ?? null, error: { code, message } });
}

function snapshotResult() {
  return {
    content: [{ type: "text", text: "Hao System read-only working projection." }],
    structuredContent: { snapshot: SNAPSHOT }
  };
}

function routeResult(args = {}) {
  const route = routeTask(args);
  const label = route.lane ?? route.disposition ?? "UNRESOLVED";
  return {
    content: [{ type: "text", text: `Routing result: ${label}` }],
    structuredContent: { route, policy: ROUTER_POLICY }
  };
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, "http://localhost");

  if (req.method === "OPTIONS") {
    cors(res);
    res.statusCode = 204;
    return res.end();
  }

  if (url.pathname === "/healthz") {
    return sendJson(res, 200, {
      ok: true,
      version: VERSION,
      releaseCommit: RELEASE_COMMIT,
      artifactRole: SNAPSHOT.artifactRole,
      formalAuthority: SNAPSHOT.formalAuthority,
      projectionFreshness: SNAPSHOT.projectionFreshness,
      systemAdminScope: SNAPSHOT.systemAdmin.surfaceScope,
      maintenanceMutation: SNAPSHOT.systemAdmin.maintenanceMutation,
      maintenancePreflight: SNAPSHOT.systemAdmin.maintenancePreflight,
      activeWorkAwareness: SNAPSHOT.systemAdmin.activeWorkAwareness,
      validationPlan: SNAPSHOT.systemAdmin.validationPlan,
      admissionFailClosed: SNAPSHOT.systemAdmin.admissionFailClosed,
      taskRouter: "READ_ONLY",
      widgetBytes: WIDGET_BYTES,
      widgetSha256: WIDGET_SHA256,
      widgetFidelity: "PASS"
    });
  }

  if (url.pathname === "/" && req.method === "GET") {
    return sendJson(res, 200, {
      name: "hao-system-control",
      title: "Hao System Control",
      version: VERSION,
      releaseCommit: RELEASE_COMMIT,
      mcp: "/mcp",
      health: "/healthz",
      artifactRole: SNAPSHOT.artifactRole,
      formalAuthority: SNAPSHOT.formalAuthority,
      projectionFreshness: SNAPSHOT.projectionFreshness,
      systemAdminScope: SNAPSHOT.systemAdmin.surfaceScope,
      maintenanceMutation: SNAPSHOT.systemAdmin.maintenanceMutation,
      maintenancePreflight: SNAPSHOT.systemAdmin.maintenancePreflight,
      activeWorkAwareness: SNAPSHOT.systemAdmin.activeWorkAwareness,
      validationPlan: SNAPSHOT.systemAdmin.validationPlan,
      admissionFailClosed: SNAPSHOT.systemAdmin.admissionFailClosed,
      taskRouter: "READ_ONLY",
      widgetBytes: WIDGET_BYTES,
      widgetSha256: WIDGET_SHA256,
      widgetFidelity: "PASS"
    });
  }

  if (url.pathname === "/widget-preview" && req.method === "GET") {
    cors(res);
    res.statusCode = 200;
    res.setHeader("content-type", "text/html; charset=utf-8");
    return res.end(WIDGET);
  }

  if (url.pathname !== "/mcp") {
    res.statusCode = 404;
    return res.end("Not Found");
  }

  if (req.method !== "POST") {
    res.statusCode = 405;
    return res.end("Method Not Allowed");
  }

  let raw = "";
  req.on("data", chunk => { raw += chunk; });
  req.on("end", () => {
    let msg;
    try { msg = JSON.parse(raw); }
    catch { return rpcError(res, null, -32700, "Parse error"); }

    if (msg.id === undefined && typeof msg.method === "string") {
      cors(res);
      res.statusCode = 202;
      return res.end();
    }

    const id = msg.id ?? null;
    const params = msg.params ?? {};

    if (msg.method === "initialize") {
      return rpc(res, id, {
        protocolVersion: "2025-06-18",
        serverInfo: { name: "hao-system-control", title: "Hao System Control", version: VERSION },
        capabilities: {
          tools: { listChanged: false },
          resources: { subscribe: false, listChanged: false }
        }
      });
    }

    if (msg.method === "tools/list") return rpc(res, id, { tools: TOOLS });

    if (msg.method === "resources/list") {
      return rpc(res, id, {
        resources: [{
          uri: RESOURCE_URI,
          name: "Hao System Control v3",
          mimeType: RESOURCE_MIME,
          description: "Read-only Hao System EXP control widget."
        }]
      });
    }

    if (msg.method === "resources/read") {
      if (params.uri !== RESOURCE_URI) return rpcError(res, id, -32002, "Resource not found");
      return rpc(res, id, { contents: [{ uri: RESOURCE_URI, mimeType: RESOURCE_MIME, text: WIDGET }] });
    }

    if (msg.method === "tools/call") {
      if (params.name === "get_hao_system_snapshot") return rpc(res, id, snapshotResult());
      if (params.name === "render_hao_system_control") {
        return rpc(res, id, { ...snapshotResult(), _meta: { "openai/outputTemplate": RESOURCE_URI } });
      }
      if (params.name === "route_hao_task") return rpc(res, id, routeResult(params.arguments ?? {}));
      return rpcError(res, id, -32602, "Unknown tool");
    }

    if (msg.method === "ping") return rpc(res, id, {});
    return rpcError(res, id, -32601, "Method not found");
  });
});

server.listen(PORT, HOST, () => {
  console.log(JSON.stringify({
    event: "startup",
    port: PORT,
    host: HOST,
    version: VERSION,
    releaseCommit: RELEASE_COMMIT,
    artifactRole: SNAPSHOT.artifactRole,
    formalAuthority: SNAPSHOT.formalAuthority,
    projectionFreshness: SNAPSHOT.projectionFreshness,
    systemAdminScope: SNAPSHOT.systemAdmin.surfaceScope,
    maintenanceMutation: SNAPSHOT.systemAdmin.maintenanceMutation,
    maintenancePreflight: SNAPSHOT.systemAdmin.maintenancePreflight,
    activeWorkAwareness: SNAPSHOT.systemAdmin.activeWorkAwareness,
    validationPlan: SNAPSHOT.systemAdmin.validationPlan,
    admissionFailClosed: SNAPSHOT.systemAdmin.admissionFailClosed,
    taskRouter: "READ_ONLY",
    widgetBytes: WIDGET_BYTES,
    widgetSha256: WIDGET_SHA256,
    widgetFidelity: "PASS"
  }));
});
