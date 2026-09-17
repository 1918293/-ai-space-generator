import http from 'node:http';

const port = Number(process.env.PORT || 9091);
const protectedStore = new Map();
const unprotectedStore = new Map();
const attempts = new Map();

function json(res, status, body) {
  const data = Buffer.from(JSON.stringify(body));
  res.writeHead(status, {
    'content-type': 'application/json',
    'content-length': data.length,
  });
  res.end(data);
}

async function readJson(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const raw = Buffer.concat(chunks).toString('utf8');
  return raw ? JSON.parse(raw) : {};
}

function attemptKey(lane, actionId) {
  return `${lane}:${actionId}`;
}

function incrementAttempt(lane, actionId) {
  const key = attemptKey(lane, actionId);
  const next = (attempts.get(key) || 0) + 1;
  attempts.set(key, next);
  return next;
}

function stateFor(lane, actionId) {
  const attemptCount = attempts.get(attemptKey(lane, actionId)) || 0;
  if (lane === 'protected') {
    const row = protectedStore.get(actionId);
    return {
      lane,
      action_id: actionId,
      attempt_count: attemptCount,
      logical_count: row ? 1 : 0,
      payload_hashes: row ? [row.payload_hash] : [],
    };
  }
  const rows = unprotectedStore.get(actionId) || [];
  return {
    lane,
    action_id: actionId,
    attempt_count: attemptCount,
    logical_count: rows.length,
    payload_hashes: rows.map((row) => row.payload_hash),
  };
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://${req.headers.host || '127.0.0.1'}`);

    if (req.method === 'GET' && url.pathname === '/health') {
      return json(res, 200, { ok: true });
    }

    if (req.method === 'GET' && url.pathname === '/state') {
      const lane = url.searchParams.get('lane');
      const actionId = url.searchParams.get('action_id');
      if (!['protected', 'unprotected'].includes(lane) || !actionId) {
        return json(res, 400, { error: 'lane and action_id required' });
      }
      return json(res, 200, stateFor(lane, actionId));
    }

    const match = req.method === 'POST' && url.pathname.match(/^\/(protected|unprotected)\/write$/);
    if (match) {
      const lane = match[1];
      const body = await readJson(req);
      const actionId = body.action_id;
      const payloadHash = body.payload_hash;
      if (!actionId || !payloadHash) {
        return json(res, 400, { error: 'action_id and payload_hash required' });
      }

      const attempt = incrementAttempt(lane, actionId);
      if (lane === 'protected') {
        const existing = protectedStore.get(actionId);
        if (existing && existing.payload_hash !== payloadHash) {
          return json(res, 409, {
            error: 'IDEMPOTENCY_CONFLICT',
            action_id: actionId,
            existing_payload_hash: existing.payload_hash,
            incoming_payload_hash: payloadHash,
          });
        }
        if (!existing) {
          protectedStore.set(actionId, { action_id: actionId, payload_hash: payloadHash });
        }
      } else {
        const rows = unprotectedStore.get(actionId) || [];
        rows.push({ action_id: actionId, payload_hash: payloadHash, attempt });
        unprotectedStore.set(actionId, rows);
      }

      const state = stateFor(lane, actionId);
      console.log(JSON.stringify({ event: 'mock_write', lane, action_id: actionId, attempt, state }));

      if (attempt === 1) {
        return json(res, 500, {
          error: 'SIMULATED_LOST_ACK_AFTER_COMMIT',
          committed: true,
          ...state,
        });
      }

      return json(res, 200, { ok: true, ...state });
    }

    return json(res, 404, { error: 'not_found' });
  } catch (error) {
    console.error(error);
    return json(res, 500, { error: String(error?.message || error) });
  }
});

server.listen(port, '0.0.0.0', () => {
  console.log(JSON.stringify({ event: 'mock_target_ready', port }));
});
