'use strict';

const VERSION = '0.3.0';
const $ = (id) => document.getElementById(id);
const state = {
  original: null,
  current: null,
  history: [],
  tool: 'edit-brush',
  drawing: false,
  lastPoint: null,
  deferredInstall: null,
  source: null,
  activeRun: null,
  qa: null,
  maskRevision: 0,
  runSequence: 0,
};

const imageInput = $('imageInput');
const photoCanvas = $('photoCanvas');
const maskCanvas = $('maskCanvas');
const protectedCanvas = $('protectedCanvas');
const beforeCanvas = $('beforeCanvas');
const afterCanvas = $('afterCanvas');
const photoCtx = photoCanvas.getContext('2d', { willReadFrequently: true });
const maskCtx = maskCanvas.getContext('2d', { willReadFrequently: true });
const protectedCtx = protectedCanvas.getContext('2d', { willReadFrequently: true });
const beforeCtx = beforeCanvas.getContext('2d');
const afterCtx = afterCanvas.getContext('2d');

function cloneImageData(image) {
  return new ImageData(new Uint8ClampedArray(image.data), image.width, image.height);
}

function setStatus(message, error = false) {
  $('status').textContent = message;
  $('status').style.color = error ? 'var(--danger)' : '';
}

function setButtons() {
  const ready = !!state.original;
  $('generateBtn').disabled = !ready;
  $('resetBtn').disabled = !ready;
  $('undoBtn').disabled = state.history.length === 0;
  $('visualQa').disabled = !state.activeRun;
  $('downloadBtn').disabled = !state.activeRun;
  $('downloadManifestBtn').disabled = !state.activeRun;
  $('downloadEditMaskBtn').disabled = !state.activeRun;
  $('downloadProtectedMaskBtn').disabled = !state.activeRun;
  $('downloadBtn').textContent = state.qa?.overall === 'PASS' ? '下載 PASS PNG' : state.activeRun ? '下載候選 PNG' : '下載 PNG';
}

async function sha256Hex(bytes) {
  if (!globalThis.crypto?.subtle) throw new Error('此瀏覽器不支援 SHA-256。');
  const digest = await crypto.subtle.digest('SHA-256', bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
  return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
}

async function loadFile(file) {
  if (!file || !file.type.startsWith('image/')) return setStatus('請選擇有效圖片。', true);
  const url = URL.createObjectURL(file);
  const img = new Image();
  img.onload = () => {
    const width = img.naturalWidth;
    const height = img.naturalHeight;
    [photoCanvas, maskCanvas, protectedCanvas, beforeCanvas, afterCanvas].forEach(c => { c.width = width; c.height = height; });
    photoCtx.drawImage(img, 0, 0, width, height);
    state.original = photoCtx.getImageData(0, 0, width, height);
    state.current = cloneImageData(state.original);
    state.history = [];
    state.activeRun = null;
    state.qa = null;
    state.maskRevision = 0;
    state.runSequence = 0;
    state.source = { filename: file.name, mime_type: file.type || 'application/octet-stream', size: file.size, last_modified: file.lastModified };
    clearMasks(false);
    renderCurrent();
    renderMeta(file, img);
    renderQa(null);
    $('visualQa').value = 'PENDING';
    $('emptyState').classList.add('hidden');
    $('canvasWrap').classList.remove('hidden');
    $('resultSection').classList.add('hidden');
    setButtons();
    setStatus('照片已以原始解析度載入。請設定 Edit Mask 與 Protected Mask。');
    URL.revokeObjectURL(url);
  };
  img.onerror = () => { URL.revokeObjectURL(url); setStatus('無法讀取圖片。', true); };
  img.src = url;
}

function renderMeta(file, img) {
  const kb = Math.round(file.size / 1024).toLocaleString('zh-TW');
  $('fileMeta').innerHTML = `<span>檔名：${escapeHtml(file.name)}</span><span>大小：${kb} KB</span><span>原始／處理：${img.naturalWidth} × ${img.naturalHeight}</span><span>格式：${escapeHtml(file.type || '未知')}</span>`;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;' }[c]));
}

function renderCurrent() {
  if (state.current) photoCtx.putImageData(state.current, 0, 0);
}

function clearEditMask(note = true) {
  maskCtx.clearRect(0, 0, maskCanvas.width, maskCanvas.height);
  if (note) noteMaskRevision('Edit Mask 已清除。');
}

function clearProtectedMask(note = true) {
  protectedCtx.clearRect(0, 0, protectedCanvas.width, protectedCanvas.height);
  if (note) noteMaskRevision('Protected Mask 已清除。');
}

function clearMasks(note = true) {
  clearEditMask(false);
  clearProtectedMask(false);
  if (note) noteMaskRevision('所有遮罩已清除。');
}

function noteMaskRevision(message) {
  state.maskRevision += 1;
  setStatus(state.activeRun ? `${message} 現有候選仍使用建立當下的凍結遮罩。` : message);
}

function canvasPoint(event) {
  const rect = maskCanvas.getBoundingClientRect();
  return { x: (event.clientX - rect.left) * maskCanvas.width / rect.width, y: (event.clientY - rect.top) * maskCanvas.height / rect.height };
}

function drawMaskSegment(from, to) {
  const protectedTool = state.tool.startsWith('protect-');
  const erase = state.tool.endsWith('-erase');
  const ctx = protectedTool ? protectedCtx : maskCtx;
  ctx.save();
  ctx.globalCompositeOperation = erase ? 'destination-out' : 'source-over';
  ctx.strokeStyle = protectedTool ? 'rgba(255,110,110,.52)' : 'rgba(200,255,114,.58)';
  ctx.lineWidth = Number($('brushSize').value);
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.beginPath();
  ctx.moveTo(from.x, from.y);
  ctx.lineTo(to.x, to.y);
  ctx.stroke();
  ctx.restore();
}

function beginStroke(event) {
  if (!state.original || event.button > 0) return;
  event.preventDefault();
  state.drawing = true;
  state.lastPoint = canvasPoint(event);
  drawMaskSegment(state.lastPoint, state.lastPoint);
  noteMaskRevision('遮罩已修改。');
  try { maskCanvas.setPointerCapture?.(event.pointerId); } catch (_) {}
}

function moveStroke(event) {
  if (!state.drawing || !state.lastPoint) return;
  event.preventDefault();
  const point = canvasPoint(event);
  drawMaskSegment(state.lastPoint, point);
  state.lastPoint = point;
}

function endStroke(event) {
  if (!state.drawing) return;
  state.drawing = false;
  state.lastPoint = null;
  try { maskCanvas.releasePointerCapture?.(event.pointerId); } catch (_) {}
}

function maskBinary(ctx) {
  const data = ctx.getImageData(0, 0, ctx.canvas.width, ctx.canvas.height).data;
  const binary = new Uint8Array(data.length / 4);
  for (let p = 0, i = 3; i < data.length; p++, i += 4) binary[p] = data[i] > 15 ? 1 : 0;
  return binary;
}

function countMask(mask) {
  let n = 0;
  for (const value of mask) n += value ? 1 : 0;
  return n;
}

function overlapPixels(a, b) {
  let n = 0;
  for (let i = 0; i < a.length; i++) if (a[i] && b[i]) n++;
  return n;
}

function cloneRun(run) {
  if (!run) return null;
  return { ...run, editMask: new Uint8Array(run.editMask), protectedMask: new Uint8Array(run.protectedMask), qa: run.qa ? { ...run.qa } : null, manifest: run.manifest ? JSON.parse(JSON.stringify(run.manifest)) : null };
}

function saveHistory() {
  if (!state.activeRun) return;
  state.history.push({ image: cloneImageData(state.current), run: cloneRun(state.activeRun) });
  if (state.history.length > 12) state.history.shift();
}

async function generate() {
  if (!state.original) return;
  const editMask = maskBinary(maskCtx);
  const protectedMask = maskBinary(protectedCtx);
  if (!countMask(editMask)) return setStatus('Edit Mask 為空，請先選取修改範圍。', true);
  const overlap = overlapPixels(editMask, protectedMask);
  if (overlap) {
    renderQa({ preflight: true, dimensions_match: true, outside_edit_mask_changed_pixels: null, protected_region_changed_pixels: null, edit_protected_overlap_pixels: overlap, pixel_qa: 'FAIL', visual_qa: 'NOT_RUN', overall: 'FAIL' });
    return setStatus(`Edit Mask 與 Protected Mask 重疊 ${overlap.toLocaleString()} px；未建立新候選。`, true);
  }

  saveHistory();
  state.runSequence += 1;
  $('visualQa').value = 'PENDING';
  const output = cloneImageData(state.original);
  applyOperation(output, editMask);
  state.current = output;
  renderCurrent();
  renderComparison();

  const [editSha, protectedSha] = await Promise.all([sha256Hex(editMask), sha256Hex(protectedMask)]);
  const run = {
    runSequence: state.runSequence,
    maskRevision: state.maskRevision,
    createdAt: new Date().toISOString(),
    operation: $('operation').value,
    prompt: $('prompt').value.trim(),
    editMask: new Uint8Array(editMask),
    protectedMask: new Uint8Array(protectedMask),
    editMaskSha256: editSha,
    protectedMaskSha256: protectedSha,
    qa: null,
    manifest: null,
  };
  state.activeRun = run;
  state.qa = validateOutput(state.original, state.current, run.editMask, run.protectedMask, 0);
  run.qa = { ...state.qa };
  run.manifest = buildManifest(run, state.qa);
  renderQa(state.qa);
  setButtons();
  setStatus('已從不可變原圖建立新候選；Visual QA 已重設為 PENDING。');
}

function applyOperation(output, editMask) {
  const source = state.original.data;
  const data = output.data;
  const width = output.width;
  const height = output.height;
  const op = $('operation').value;
  const color = hexToRgb($('fillColor').value);
  for (let p = 0; p < editMask.length; p++) {
    if (!editMask[p]) continue;
    const i = p * 4;
    const x = p % width;
    const y = Math.floor(p / width);
    let target;
    if (op === 'remove') target = neighborhoodAverage(source, width, height, x, y, 20);
    else if (op === 'color') {
      const lum = (source[i] + source[i+1] + source[i+2]) / 765;
      target = [color.r * (.55 + lum * .55), color.g * (.55 + lum * .55), color.b * (.55 + lum * .55)];
    } else if (op === 'wood') {
      const g = 22 * Math.sin(y * .18 + Math.sin(x * .03)) + 10 * Math.sin(y * .035);
      target = [161 + g, 116 + g * .55, 73 + g * .25];
    } else if (op === 'stone') {
      const n = ((x * 17 + y * 31) % 29) - 14;
      target = [176 + n, 178 + n, 174 + n];
    } else if (op === 'fabric') {
      const w = (x % 7 < 2 || y % 7 < 2) ? -26 : 8;
      target = [154 + w, 144 + w, 132 + w];
    } else target = [Math.min(255, source[i] * 1.28 + 18), Math.min(255, source[i+1] * 1.25 + 18), Math.min(255, source[i+2] * 1.18 + 14)];
    data[i] = mix(source[i], target[0], .92);
    data[i+1] = mix(source[i+1], target[1], .92);
    data[i+2] = mix(source[i+2], target[2], .92);
  }
}

function neighborhoodAverage(data, width, height, x, y, radius) {
  const samples = [[-radius,0],[radius,0],[0,-radius],[0,radius],[-radius,-radius],[radius,radius]];
  let r = 0, g = 0, b = 0;
  for (const [dx,dy] of samples) {
    const sx = Math.max(0, Math.min(width - 1, x + dx));
    const sy = Math.max(0, Math.min(height - 1, y + dy));
    const j = (sy * width + sx) * 4;
    r += data[j]; g += data[j+1]; b += data[j+2];
  }
  return [r / samples.length, g / samples.length, b / samples.length];
}

function mix(a, b, t) { return Math.round(a * (1 - t) + b * t); }
function hexToRgb(hex) { const v = parseInt(hex.slice(1), 16); return { r:(v>>16)&255, g:(v>>8)&255, b:v&255 }; }

function validateOutput(original, output, editMask, protectedMask, overlap) {
  const dimensions = original.width === output.width && original.height === output.height;
  let outside = 0, protectedChanged = 0;
  if (dimensions) {
    for (let p = 0; p < editMask.length; p++) {
      const i = p * 4;
      const changed = original.data[i] !== output.data[i] || original.data[i+1] !== output.data[i+1] || original.data[i+2] !== output.data[i+2] || original.data[i+3] !== output.data[i+3];
      if (changed && !editMask[p]) outside++;
      if (changed && protectedMask[p]) protectedChanged++;
    }
  }
  const pixelPass = dimensions && outside === 0 && protectedChanged === 0 && overlap === 0;
  const visual = $('visualQa').value;
  return {
    dimensions_match: dimensions,
    outside_edit_mask_changed_pixels: outside,
    protected_region_changed_pixels: protectedChanged,
    edit_protected_overlap_pixels: overlap,
    pixel_qa: pixelPass ? 'PASS' : 'FAIL',
    visual_qa: visual,
    overall: !pixelPass || visual === 'FAIL' ? 'FAIL' : visual === 'PASS' ? 'PASS' : 'PENDING',
  };
}

function buildManifest(run, qa) {
  const total = state.original.width * state.original.height;
  return {
    schema_version: '1.1',
    app_version: VERSION,
    run_id: `run-${run.runSequence}-${run.createdAt.replace(/[:.]/g, '-')}`,
    created_at: run.createdAt,
    source: { ...state.source, width: state.original.width, height: state.original.height, immutable: true },
    goal: run.operation,
    prompt: run.prompt,
    constraints: { preserve_dimensions: true, preserve_outside_edit_mask_pixels: true, preserve_protected_pixels: true, source_each_candidate_from_original: true },
    masks: { revision: run.maskRevision, edit_sha256: run.editMaskSha256, protected_sha256: run.protectedMaskSha256, edit_pixels: countMask(run.editMask), protected_pixels: countMask(run.protectedMask), edit_coverage_ratio: countMask(run.editMask) / total, protected_coverage_ratio: countMask(run.protectedMask) / total },
    qa: { ...qa },
    output: { classification: qa.overall === 'PASS' ? 'validated' : 'candidate' },
  };
}

function renderQa(qa) {
  const box = $('qaResults');
  if (!qa) { box.innerHTML = '<span class="notice">尚未建立候選。</span>'; return; }
  const run = qa.preflight ? 'PRE-FLIGHT' : state.activeRun ? `#${state.activeRun.runSequence} · mask r${state.activeRun.maskRevision}` : '—';
  box.innerHTML = `<div><span>Run</span><strong>${run}</strong></div><div><span>Dimensions</span><strong>${qa.dimensions_match ? 'PASS' : 'FAIL'}</strong></div><div><span>Outside Edit</span><strong>${qa.outside_edit_mask_changed_pixels ?? 'N/A'} px</strong></div><div><span>Protected</span><strong>${qa.protected_region_changed_pixels ?? 'N/A'} px</strong></div><div><span>Overlap</span><strong>${qa.edit_protected_overlap_pixels ?? 'N/A'} px</strong></div><div><span>Pixel QA</span><strong>${escapeHtml(qa.pixel_qa)}</strong></div><div><span>Visual QA</span><strong>${escapeHtml(qa.visual_qa)}</strong></div><div><span>Overall</span><strong>${escapeHtml(qa.overall)}</strong></div>`;
}

function refreshVisualQa() {
  if (!state.activeRun) return;
  const run = state.activeRun;
  state.qa = validateOutput(state.original, state.current, run.editMask, run.protectedMask, overlapPixels(run.editMask, run.protectedMask));
  run.qa = { ...state.qa };
  run.manifest = buildManifest(run, state.qa);
  renderQa(state.qa);
  setButtons();
}

function renderComparison() {
  beforeCtx.putImageData(state.original, 0, 0);
  afterCtx.putImageData(state.current, 0, 0);
  $('resultSection').classList.remove('hidden');
  updateComparison($('compareRange').value);
}

function updateComparison(value) {
  $('afterLayer').style.width = `${value}%`;
  $('compareLine').style.left = `${value}%`;
}

function restoreMask(ctx, binary, rgb) {
  const image = ctx.createImageData(ctx.canvas.width, ctx.canvas.height);
  for (let p = 0; p < binary.length; p++) {
    const i = p * 4;
    image.data[i] = rgb[0]; image.data[i+1] = rgb[1]; image.data[i+2] = rgb[2]; image.data[i+3] = binary[p] ? 140 : 0;
  }
  ctx.putImageData(image, 0, 0);
}

function undo() {
  if (!state.history.length) return;
  const snapshot = state.history.pop();
  state.current = snapshot.image;
  state.activeRun = snapshot.run;
  state.qa = snapshot.run?.qa || null;
  state.maskRevision = snapshot.run?.maskRevision ?? state.maskRevision;
  if (state.activeRun) {
    restoreMask(maskCtx, state.activeRun.editMask, [200,255,114]);
    restoreMask(protectedCtx, state.activeRun.protectedMask, [255,110,110]);
    $('visualQa').value = state.qa?.visual_qa || 'PENDING';
  }
  renderCurrent(); renderComparison(); renderQa(state.qa); setButtons();
  setStatus('已復原候選、Run、QA、Manifest 與凍結遮罩快照。');
}

function reset() {
  if (!state.original) return;
  state.current = cloneImageData(state.original);
  state.history = [];
  state.activeRun = null;
  state.qa = null;
  state.maskRevision = 0;
  state.runSequence = 0;
  $('visualQa').value = 'PENDING';
  renderCurrent(); clearMasks(false); renderQa(null);
  $('resultSection').classList.add('hidden');
  setButtons(); setStatus('已回到不可變原始照片。');
}

function downloadImage() {
  if (!state.activeRun) return;
  const canvas = document.createElement('canvas');
  canvas.width = state.current.width; canvas.height = state.current.height;
  canvas.getContext('2d').putImageData(state.current, 0, 0);
  const suffix = state.qa?.overall === 'PASS' ? 'validated' : 'candidate';
  downloadHref(canvas.toDataURL('image/png'), `${state.activeRun.manifest.run_id}-${suffix}.png`);
}

function downloadJson() {
  if (!state.activeRun) return;
  const blob = new Blob([JSON.stringify(state.activeRun.manifest, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  downloadHref(url, `${state.activeRun.manifest.run_id}-manifest.json`);
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

function downloadMask(kind) {
  if (!state.activeRun) return;
  const binary = kind === 'protected' ? state.activeRun.protectedMask : state.activeRun.editMask;
  const canvas = document.createElement('canvas');
  canvas.width = state.original.width; canvas.height = state.original.height;
  const ctx = canvas.getContext('2d');
  const image = ctx.createImageData(canvas.width, canvas.height);
  for (let p = 0; p < binary.length; p++) {
    const i = p * 4, v = binary[p] ? 255 : 0;
    image.data[i] = v; image.data[i+1] = v; image.data[i+2] = v; image.data[i+3] = 255;
  }
  ctx.putImageData(image, 0, 0);
  downloadHref(canvas.toDataURL('image/png'), `${state.activeRun.manifest.run_id}-${kind}-mask.png`);
}

function downloadHref(href, filename) {
  const link = document.createElement('a'); link.href = href; link.download = filename; link.click();
}

imageInput.addEventListener('change', e => loadFile(e.target.files?.[0]));
$('brushSize').addEventListener('input', e => { $('brushSizeValue').value = e.target.value; });
$('clearEditMaskBtn').addEventListener('click', () => clearEditMask());
$('clearProtectedMaskBtn').addEventListener('click', () => clearProtectedMask());
$('clearMaskBtn').addEventListener('click', () => clearMasks());
$('generateBtn').addEventListener('click', () => generate().catch(error => setStatus(error.message, true)));
$('undoBtn').addEventListener('click', undo);
$('resetBtn').addEventListener('click', reset);
$('downloadBtn').addEventListener('click', downloadImage);
$('downloadManifestBtn').addEventListener('click', downloadJson);
$('downloadEditMaskBtn').addEventListener('click', () => downloadMask('edit'));
$('downloadProtectedMaskBtn').addEventListener('click', () => downloadMask('protected'));
$('visualQa').addEventListener('change', refreshVisualQa);
$('compareRange').addEventListener('input', e => updateComparison(e.target.value));
$('operation').addEventListener('change', () => $('colorField').classList.toggle('hidden', $('operation').value !== 'color'));

document.querySelectorAll('[data-tool]').forEach(button => button.addEventListener('click', () => {
  state.tool = button.dataset.tool;
  document.querySelectorAll('[data-tool]').forEach(item => item.classList.toggle('active', item === button));
}));

maskCanvas.addEventListener('pointerdown', beginStroke, { passive: false });
maskCanvas.addEventListener('pointermove', moveStroke, { passive: false });
['pointerup','pointercancel','pointerleave'].forEach(name => maskCanvas.addEventListener(name, endStroke, { passive: false }));

window.addEventListener('beforeinstallprompt', event => { event.preventDefault(); state.deferredInstall = event; $('installBtn').classList.remove('hidden'); });
$('installBtn').addEventListener('click', async () => {
  if (!state.deferredInstall) return;
  state.deferredInstall.prompt(); await state.deferredInstall.userChoice;
  state.deferredInstall = null; $('installBtn').classList.add('hidden');
});
if ('serviceWorker' in navigator) window.addEventListener('load', () => navigator.serviceWorker.register('./sw.js').catch(() => {}));

$('colorField').classList.add('hidden');
renderQa(null);
setButtons();
