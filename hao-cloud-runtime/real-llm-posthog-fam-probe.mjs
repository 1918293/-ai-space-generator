import crypto from 'node:crypto';

const apiKey = process.env.GEMINI_API_KEY;
const posthogKey = process.env.POSTHOG_PROJECT_API_KEY;
const posthogHost = process.env.POSTHOG_HOST || 'https://eu.i.posthog.com';
const runKey = process.env.FAM_RUN_KEY || `FAM-${process.env.GITHUB_RUN_ID || crypto.randomUUID()}`;
const traceId = crypto.randomUUID();
const generationId = crypto.randomUUID();

if (!apiKey) throw new Error('GEMINI_API_KEY missing');
if (!posthogKey) throw new Error('POSTHOG_PROJECT_API_KEY missing');

const started = Date.now();
const model = 'gemini-2.5-flash-lite';
const input = 'Reply with exactly: HAO_FAM_REAL_LLM_PASS';
const response = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`, {
  method: 'POST',
  headers: { 'x-goog-api-key': apiKey, 'content-type': 'application/json' },
  body: JSON.stringify({ contents: [{ parts: [{ text: input }] }], generationConfig: { maxOutputTokens: 32, temperature: 0 } }),
  signal: AbortSignal.timeout(60000),
});
const body = await response.json();
if (!response.ok) throw new Error(`Gemini call failed: ${response.status} ${JSON.stringify(body).slice(0,500)}`);
const output = body.candidates?.[0]?.content?.parts?.map(p => p.text ?? '').join('') ?? '';
const latency = (Date.now() - started) / 1000;
const usage = body.usageMetadata || {};

const event = {
  api_key: posthogKey,
  event: '$ai_generation',
  properties: {
    distinct_id: `hao-fam-real-llm-${runKey}`,
    '$ai_trace_id': traceId,
    '$ai_generation_id': generationId,
    '$ai_model': model,
    '$ai_provider': 'google',
    '$ai_input': input,
    '$ai_output_choices': [{ role: 'assistant', content: output }],
    '$ai_latency': latency,
    '$ai_input_tokens': usage.promptTokenCount ?? null,
    '$ai_output_tokens': usage.candidatesTokenCount ?? null,
    '$ai_is_error': false,
    '$ai_trace_name': 'Hao FAM real LLM observation E2E',
    run_key: runKey,
    environment: 'fam',
    is_synthetic: false,
    measurement_source: 'real_gemini_api_call_free_tier',
    production_canonical_target_touched: false
  }
};
const capture = await fetch(`${posthogHost.replace(/\/$/, '')}/i/v0/e/`, {
  method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(event), signal: AbortSignal.timeout(30000)
});
if (!capture.ok) throw new Error(`PostHog capture failed: ${capture.status} ${(await capture.text()).slice(0,500)}`);
console.log(JSON.stringify({ event: 'hao_fam_real_llm_probe', result: 'PASS', runKey, traceId, generationId, provider: 'google', model, inputTokens: usage.promptTokenCount ?? null, outputTokens: usage.candidatesTokenCount ?? null, outputMatched: output.trim() === 'HAO_FAM_REAL_LLM_PASS', productionCanonicalTargetTouched: false }, null, 2));
