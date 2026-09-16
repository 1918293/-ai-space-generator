import http from "node:http";
import { routeTask, ROUTER_POLICY } from "../hao-cloud-runtime/task-router.mjs";

const PORT = Number(process.env.PORT || 10000);
const HOST = "0.0.0.0";
const RESOURCE_URI = "ui://widget/hao-system-control-v3.html";
const RESOURCE_MIME = "text/html;profile=mcp-app";
const VERSION = "0.4.0-router-exp";
const RELEASE_COMMIT = process.env.RENDER_GIT_COMMIT || "unknown";

const SNAPSHOT = {
  artifactRole: "READ_ONLY_WORKING_PROJECTION",
  formalAuthority: "Google Drive",
  lifecycle: "EXP",
  owner: "Hao",
  releaseVersion: VERSION,
  releaseCommit: RELEASE_COMMIT,
  currentFocus: "No-computer unified task routing + free read-only control surface",
  nextAction: "Route tasks by privacy, compute, hosting, and formal-write requirements without bypassing the existing Single Write Gateway.",
  architecture: ["ChatGPT private lane", "GitHub Actions public compute", "Render read-only control", "Existing Single Write Gateway"],
  sources: ["Google Drive (formal authority)"],
  routerPolicy: ROUTER_POLICY
};

const WIDGET = `<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Hao System Control EXP</title></head><body><main><h1>Hao System Control</h1><p>READ_ONLY_WORKING_PROJECTION</p><p>Formal Authority: Google Drive</p><p>No-computer EXP control surface with read-only task routing.</p></main></body></html>`;

const ROUTE_PROPERTIES = {
  requiresLocalDevice: { type: "boolean" },
  formalMutation: { type: "boolean" },
  privateData: { type: "boolean" },
  personalData: { type: "boolean" },
  containsSecrets: { type: "boolean" },
  connectedApps: { type: "boolean" },
  longRunningBrowser: { type: "boolean" },
  publicReadOnlyService: { type: "boolean" },
  deterministicCompute: { type: "boolean" }
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
    description: "Classify a task into the Hao no-computer execution lane. Read-only classification only; this tool never performs the routed action.",
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
  return {
    content: [{ type: "text", text: `Recommended lane: ${route.lane}` }],
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
      taskRouter: "READ_ONLY"
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
      taskRouter: "READ_ONLY"
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
    taskRouter: "READ_ONLY"
  }));
});
