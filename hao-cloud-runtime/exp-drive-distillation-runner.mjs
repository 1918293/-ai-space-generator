import fs from 'node:fs';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';

const geminiKey = process.env.GEMINI_API_KEY;
const posthogKey = process.env.POSTHOG_PROJECT_API_KEY;
const model = 'gemini-3.5-flash-lite';
const runKey = `EXP-DRIVE-DISTILL-${process.env.GITHUB_RUN_ID ?? 'local'}`;
const traceId = crypto.randomUUID();
const generationId = crypto.randomUUID();
const sourceLabel = 'EXP Seed 001';
const input = fs.readFileSync('hao-cloud-runtime/exp-drive-distillation-input.txt', 'utf8');

assert.ok(geminiKey, 'GEMINI_API_KEY missing');
assert.ok(posthogKey, 'POSTHOG_PROJECT_API_KEY missing');

const prompt = `You are a bounded document-distillation engine. Return valid JSON only, with exactly these keys:\nsource\npurpose\nkey_observations\nmaterial_delta\nexisting_content_overlap\nuncertainty\ncandidate_next_action\ncanonical_write_recommendation\n\nRules:\n- source must equal "${sourceLabel}".\n- key_observations must be an array of concise strings.\n- canonical_write_recommendation must be one of: YES, NO, REVIEW.\n- Do not invent facts beyond the supplied document.\n- This is EXP-only analysis. Do not claim any authoritative record was changed.\n\nDOCUMENT:\n${input}`;

const geminiUrl = `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${geminiKey}`;
const response = await fetch(geminiUrl, {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({
    contents: [{ parts: [{ text: prompt }] }],
    generationConfig: { temperature: 0.1, maxOutputTokens: 700, responseMimeType: 'application/json' },
  }),
  signal: AbortSignal.timeout(60_000),
});
const raw = await response.text();
if (!response.ok) throw new Error(`Gemini ${response.status}: ${raw.slice(0, 1000)}`);
const body = JSON.parse(raw);
const text = body?.candidates?.[0]?.content?.parts?.map((p) => p?.text ?? '').join('').trim();
assert.ok(text, 'Gemini returned empty output');
let distillation;
try { distillation = JSON.parse(text); }
catch { throw new Error(`Gemini output was not valid JSON: ${text.slice(0, 1000)}`); }

const expectedKeys = ['source','purpose','key_observations','material_delta','existing_content_overlap','uncertainty','candidate_next_action','canonical_write_recommendation'];
assert.deepEqual(Object.keys(distillation).sort(), [...expectedKeys].sort(), 'unexpected output schema');
assert.equal(distillation.source, sourceLabel);
assert.ok(Array.isArray(distillation.key_observations));
assert.ok(['YES','NO','REVIEW'].includes(distillation.canonical_write_recommendation));

const usage = body?.usageMetadata ?? {};
const output = {
  schema: 'hao-exp-drive-distillation-v1',
  mode: 'EXP',
  source_label: sourceLabel,
  run_key: runKey,
  trace_id: traceId,
  generation_id: generationId,
  provider: 'google',
  model,
  input_tokens: usage.promptTokenCount ?? null,
  output_tokens: usage.candidatesTokenCount ?? null,
  distillation,
  production_canonical_target_touched: false,
};

fs.mkdirSync('exp-drive-distillation-output', { recursive: true });
fs.writeFileSync('exp-drive-distillation-output/distillation.json', JSON.stringify(output, null, 2));

const capture = await fetch('https://eu.i.posthog.com/i/v0/e/', {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({
    api_key: posthogKey,
    event: '$ai_generation',
    distinct_id: `exp-drive-distill-${runKey}`,
    properties: {
      '$ai_trace_id': traceId,
      '$ai_generation_id': generationId,
      '$ai_trace_name': 'EXP Drive Distillation Pilot',
      '$ai_provider': 'google',
      '$ai_model': model,
      '$ai_input': prompt,
      '$ai_output_choices': [{ role: 'assistant', content: JSON.stringify(distillation) }],
      '$ai_input_tokens': usage.promptTokenCount ?? null,
      '$ai_output_tokens': usage.candidatesTokenCount ?? null,
      '$ai_is_error': false,
      run_key: runKey,
      source_label: sourceLabel,
      input_class: 'synthetic_non_sensitive',
      is_synthetic: false,
      measurement_source: 'chat_native_drive_github_gemini_exp',
      production_canonical_target_touched: false,
    },
  }),
  signal: AbortSignal.timeout(30_000),
});
assert.ok(capture.ok, `PostHog capture ${capture.status}: ${(await capture.text()).slice(0,500)}`);

console.log(JSON.stringify({
  event: 'hao_exp_drive_distillation',
  result: 'PASS',
  runKey,
  traceId,
  generationId,
  model,
  inputTokens: usage.promptTokenCount ?? null,
  outputTokens: usage.candidatesTokenCount ?? null,
  distillation,
  productionCanonicalTargetTouched: false,
}, null, 2));
