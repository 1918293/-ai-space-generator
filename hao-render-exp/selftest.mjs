const PORT = Number(process.env.PORT || 10000);
const BASE = `http://127.0.0.1:${PORT}`;

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

async function run() {
  const results = [];
  try {
    const health = await fetch(`${BASE}/healthz`);
    const hb = await health.json();
    results.push(check("healthz", health.status === 200 && hb.ok === true && hb.formalAuthority === "Google Drive", { status: health.status }));

    const init = await post({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "render-selftest", version: "0.1" } } });
    results.push(check("initialize", init.status === 200 && init.body?.result?.protocolVersion === "2025-06-18" && init.body?.result?.serverInfo?.name === "hao-system-control", { status: init.status }));

    const tools = await post({ jsonrpc: "2.0", id: 2, method: "tools/list", params: {} });
    const ts = tools.body?.result?.tools ?? [];
    results.push(check("tools/list", tools.status === 200 && ts.length === 2 && ts.every(t => t.annotations?.readOnlyHint === true) && ts.some(t => t.name === "get_hao_system_snapshot") && ts.some(t => t.name === "render_hao_system_control"), { status: tools.status, count: ts.length }));

    const rl = await post({ jsonrpc: "2.0", id: 3, method: "resources/list", params: {} });
    const resources = rl.body?.result?.resources ?? [];
    results.push(check("resources/list", rl.status === 200 && resources[0]?.uri === "ui://widget/hao-system-control-v3.html" && resources[0]?.mimeType === "text/html;profile=mcp-app", { status: rl.status, count: resources.length }));

    const rr = await post({ jsonrpc: "2.0", id: 4, method: "resources/read", params: { uri: "ui://widget/hao-system-control-v3.html" } });
    const content = rr.body?.result?.contents?.[0];
    results.push(check("resources/read", rr.status === 200 && content?.uri === "ui://widget/hao-system-control-v3.html" && content?.mimeType === "text/html;profile=mcp-app" && typeof content?.text === "string" && content.text.length > 0, { status: rr.status, chars: content?.text?.length ?? 0 }));

    const snap = await post({ jsonrpc: "2.0", id: 5, method: "tools/call", params: { name: "get_hao_system_snapshot", arguments: {} } });
    const s = snap.body?.result?.structuredContent?.snapshot;
    results.push(check("tools/call snapshot", snap.status === 200 && s?.artifactRole === "READ_ONLY_WORKING_PROJECTION" && s?.formalAuthority === "Google Drive", { status: snap.status }));

    const render = await post({ jsonrpc: "2.0", id: 6, method: "tools/call", params: { name: "render_hao_system_control", arguments: {} } });
    results.push(check("tools/call render", render.status === 200 && render.body?.result?._meta?.["openai/outputTemplate"] === "ui://widget/hao-system-control-v3.html", { status: render.status }));

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
