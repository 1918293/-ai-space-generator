# Codex Task — Chenghe428 v0.3

Execute this task on branch `auto/chenghe428-v0.3`.

## Safety / scope

- Read and follow `AGENTS.md` first.
- Base commit is already verified externally as `c79d282aaed20f170f6157bdd61a36d28c7729c2`.
- Do not modify GitHub Actions/workflows, secrets, production deployment, repository structure, or unrelated files.
- Apply only the patch below to `app.js`, `index.html`, and `styles.css`.
- Run `node --check app.js`.
- If `/workspace` contains `runtime_selftest.js` from the task attachment, run it and require 19/19 PASS. If it is absent, report that clearly; do not invent a passing result.
- Review `git diff` and ensure only the intended application changes remain.
- Delete this `.codex/CODEX_TASK.md` helper before the final application commit so it does not remain in the PR diff.
- Final commit message: `Improve image QA reproducibility`.
- Push `auto/chenghe428-v0.3` and open a PR to `main`.
- Stop and report if the patch does not apply cleanly or validation fails.

## Patch

```diff
--- v0.2/app.js
+++ v0.3/app.js
@@ -1,7 +1,7 @@
 'use strict';
 
 const $ = (id) => document.getElementById(id);
-const VERSION = '0.2.0';
+const VERSION = '0.3.0';
 
 const state = {
   original: null,
@@ -16,6 +16,10 @@
   sourceLastModified: null,
   lastManifest: null,
   qa: null,
+  activeRun: null,
+  maskRevision: 0,
+  runSequence: 0,
+  lastPoint: null,
 };
 
 const imageInput = $('imageInput');
@@ -40,10 +44,18 @@
   $('generateBtn').disabled = !enabled;
   $('resetBtn').disabled = !enabled;
   $('undoBtn').disabled = state.history.length === 0;
-  $('downloadBtn').disabled = !enabled;
-  $('downloadManifestBtn').disabled = !state.lastManifest;
+  $('downloadBtn').disabled = !state.activeRun;
+  $('downloadManifestBtn').disabled = !state.activeRun?.manifest;
   $('downloadEditMaskBtn').disabled = !enabled;
   $('downloadProtectedMaskBtn').disabled = !enabled;
+
+  if (state.activeRun) {
+    $('downloadBtn').textContent = state.qa?.overall === 'PASS'
+      ? '下載 PASS PNG'
+      : '下載候選 PNG';
+  } else {
+    $('downloadBtn').textContent = '下載 PNG';
+  }
 }
 
 async function sha256Hex(buffer) {
@@ -89,6 +101,10 @@
       state.sourceLastModified = file.lastModified;
       state.lastManifest = null;
       state.qa = null;
+      state.activeRun = null;
+      state.maskRevision = 0;
+      state.runSequence = 0;
+      state.lastPoint = null;
 
       clearMasks();
       renderCurrent();
@@ -172,31 +188,69 @@
 
 function canvasPoint(event) {
   const rect = maskCanvas.getBoundingClientRect();
-  const source = event.touches ? event.touches[0] : event;
   return {
-    x: (source.clientX - rect.left) * (maskCanvas.width / rect.width),
-    y: (source.clientY - rect.top) * (maskCanvas.height / rect.height),
+    x: (event.clientX - rect.left) * (maskCanvas.width / rect.width),
+    y: (event.clientY - rect.top) * (maskCanvas.height / rect.height),
   };
 }
 
-function drawMask(event) {
-  if (!state.drawing || !state.original) return;
-  event.preventDefault();
-
-  const { x, y } = canvasPoint(event);
+function noteMaskRevision(message) {
+  state.maskRevision += 1;
+  if (state.activeRun) {
+    setStatus(`${message} 現有候選仍綁定上一輪遮罩快照；再次建立候選才會使用新遮罩。`);
+  } else {
+    setStatus(message);
+  }
+}
+
+function drawMaskSegment(from, to) {
+  if (!state.original) return;
+
   const size = Number($('brushSize').value);
-
   const isProtected = state.tool.includes('protect');
   const isEraser = state.tool.includes('erase');
   const ctx = isProtected ? protectedCtx : maskCtx;
 
   ctx.save();
   ctx.globalCompositeOperation = isEraser ? 'destination-out' : 'source-over';
-  ctx.fillStyle = isProtected ? 'rgba(255,110,110,.50)' : 'rgba(200,255,114,.58)';
+  ctx.strokeStyle = isProtected ? 'rgba(255,110,110,.50)' : 'rgba(200,255,114,.58)';
+  ctx.lineWidth = size;
+  ctx.lineCap = 'round';
+  ctx.lineJoin = 'round';
   ctx.beginPath();
-  ctx.arc(x, y, size / 2, 0, Math.PI * 2);
-  ctx.fill();
+  ctx.moveTo(from.x, from.y);
+  ctx.lineTo(to.x, to.y);
+  ctx.stroke();
   ctx.restore();
+}
+
+function beginMaskStroke(event) {
+  if (!state.original) return;
+  event.preventDefault();
+  state.drawing = true;
+  state.lastPoint = canvasPoint(event);
+  drawMaskSegment(state.lastPoint, state.lastPoint);
+  noteMaskRevision('遮罩已修改。');
+  if (maskCanvas.setPointerCapture && event.pointerId !== undefined) {
+    try { maskCanvas.setPointerCapture(event.pointerId); } catch (_) {}
+  }
+}
+
+function continueMaskStroke(event) {
+  if (!state.drawing || !state.lastPoint) return;
+  event.preventDefault();
+  const point = canvasPoint(event);
+  drawMaskSegment(state.lastPoint, point);
+  state.lastPoint = point;
+}
+
+function endMaskStroke(event) {
+  if (!state.drawing) return;
+  state.drawing = false;
+  state.lastPoint = null;
+  if (maskCanvas.releasePointerCapture && event?.pointerId !== undefined) {
+    try { maskCanvas.releasePointerCapture(event.pointerId); } catch (_) {}
+  }
 }
 
 function hasMask(maskData) {
@@ -222,12 +276,41 @@
   return count;
 }
 
+function cloneRun(run) {
+  if (!run) return null;
+  return {
+    ...run,
+    editMask: new Uint8ClampedArray(run.editMask),
+    protectedMask: new Uint8ClampedArray(run.protectedMask),
+    qa: run.qa ? { ...run.qa } : null,
+    manifest: run.manifest ? JSON.parse(JSON.stringify(run.manifest)) : null,
+  };
+}
+
 function saveHistory() {
-  state.history.push(cloneImageData(state.current));
+  if (!state.activeRun || !state.current) return;
+  state.history.push({
+    image: cloneImageData(state.current),
+    run: cloneRun(state.activeRun),
+  });
   if (state.history.length > 12) state.history.shift();
 }
 
-function generate() {
+function binaryMaskBytes(maskData) {
+  const out = new Uint8Array(maskData.length / 4);
+  let p = 0;
+  for (let i = 3; i < maskData.length; i += 4) {
+    out[p++] = maskData[i] > 15 ? 1 : 0;
+  }
+  return out;
+}
+
+async function maskSha256(maskData) {
+  const bytes = binaryMaskBytes(maskData);
+  return sha256Hex(bytes.buffer);
+}
+
+async function generate() {
   if (!state.original) return;
 
   const editMask = maskCtx.getImageData(0, 0, maskCanvas.width, maskCanvas.height);
@@ -246,19 +329,24 @@
   const overlap = maskOverlapPixels(editMask.data, protectedMask.data);
   if (overlap > 0) {
     setStatus(`Edit Mask 與 Protected Mask 重疊 ${overlap.toLocaleString()} px，請先修正遮罩。`, true);
-    state.qa = {
+    const preflightQa = {
+      preflight: true,
       dimensions_match: true,
       outside_edit_mask_changed_pixels: null,
       protected_region_changed_pixels: null,
       edit_protected_overlap_pixels: overlap,
-      visual_qa: $('visualQa').value,
+      visual_qa: 'NOT_RUN',
+      pixel_qa: 'FAIL',
       overall: 'FAIL',
     };
-    renderQa(state.qa);
+    renderQa(preflightQa);
+    setButtons(true);
     return;
   }
 
   saveHistory();
+  state.runSequence += 1;
+  $('visualQa').value = 'PENDING';
 
   // IMPORTANT: every candidate is rebuilt from the immutable original.
   const output = cloneImageData(state.original);
@@ -324,20 +412,39 @@
   renderCurrent();
   renderComparison();
 
+  const frozenEditMask = new Uint8ClampedArray(editMask.data);
+  const frozenProtectedMask = new Uint8ClampedArray(protectedMask.data);
+  const [editMaskSha256, protectedMaskSha256] = await Promise.all([
+    maskSha256(frozenEditMask),
+    maskSha256(frozenProtectedMask),
+  ]);
+
   const qa = validateOutput(
     state.original,
     state.current,
-    editMask.data,
-    protectedMask.data,
+    frozenEditMask,
+    frozenProtectedMask,
     overlap
   );
 
+  const run = {
+    runSequence: state.runSequence,
+    maskRevision: state.maskRevision,
+    createdAt: new Date().toISOString(),
+    operation,
+    prompt: $('prompt').value.trim(),
+    editMask: frozenEditMask,
+    protectedMask: frozenProtectedMask,
+    editMaskSha256,
+    protectedMaskSha256,
+    qa,
+    manifest: null,
+  };
+
+  run.manifest = buildManifest(run, qa);
+  state.activeRun = run;
   state.qa = qa;
-  state.lastManifest = buildManifest(
-    editMask.data,
-    protectedMask.data,
-    qa
-  );
+  state.lastManifest = run.manifest;
 
   renderQa(qa);
   setButtons(true);
@@ -432,17 +539,17 @@
   };
 }
 
-function buildManifest(editMask, protectedMask, qa) {
-  const now = new Date();
-  const editPixels = maskCoverage(editMask);
-  const protectedPixels = maskCoverage(protectedMask);
+function buildManifest(run, qa) {
+  const editPixels = maskCoverage(run.editMask);
+  const protectedPixels = maskCoverage(run.protectedMask);
   const totalPixels = state.original.width * state.original.height;
+  const runId = `run-${run.runSequence}-${run.createdAt.replace(/[:.]/g, '-')}`;
 
   return {
-    schema_version: '1.0',
+    schema_version: '1.1',
     app_version: VERSION,
-    run_id: `run-${now.toISOString().replace(/[:.]/g, '-')}`,
-    created_at: now.toISOString(),
+    run_id: runId,
+    created_at: run.createdAt,
     source: {
       filename: state.sourceFileName,
       mime_type: state.sourceMimeType,
@@ -451,8 +558,8 @@
       height: state.original.height,
       immutable: true,
     },
-    goal: $('operation').value,
-    prompt: $('prompt').value.trim(),
+    goal: run.operation,
+    prompt: run.prompt,
     constraints: {
       preserve_dimensions: true,
       preserve_outside_edit_mask_pixels: true,
@@ -460,6 +567,9 @@
       source_each_candidate_from_original: true,
     },
     masks: {
+      revision: run.maskRevision,
+      edit_sha256: run.editMaskSha256,
+      protected_sha256: run.protectedMaskSha256,
       edit_pixels: editPixels,
       protected_pixels: protectedPixels,
       edit_coverage_ratio: editPixels / totalPixels,
@@ -483,7 +593,14 @@
     return;
   }
 
+  const runInfo = qa.preflight
+    ? '<div><span>Run</span><strong>PRE-FLIGHT</strong></div>'
+    : state.activeRun
+      ? `<div><span>Run</span><strong>#${state.activeRun.runSequence} · mask r${state.activeRun.maskRevision}</strong></div>`
+      : '';
+
   box.innerHTML = `
+    ${runInfo}
     <div><span>Dimensions</span><strong>${qa.dimensions_match ? 'PASS' : 'FAIL'}</strong></div>
     <div><span>Outside Edit Mask</span><strong>${qa.outside_edit_mask_changed_pixels ?? 'N/A'} px</strong></div>
     <div><span>Protected Region</span><strong>${qa.protected_region_changed_pixels ?? 'N/A'} px</strong></div>
@@ -495,25 +612,21 @@
 }
 
 function refreshVisualQa() {
-  if (!state.qa || !state.current) return;
-
-  const editMask = maskCtx.getImageData(0, 0, maskCanvas.width, maskCanvas.height);
-  const protectedMask = protectedCtx.getImageData(
-    0,
-    0,
-    protectedCanvas.width,
-    protectedCanvas.height
-  );
-  const overlap = maskOverlapPixels(editMask.data, protectedMask.data);
+  if (!state.activeRun || !state.current) return;
+
+  const run = state.activeRun;
+  const overlap = maskOverlapPixels(run.editMask, run.protectedMask);
 
   state.qa = validateOutput(
     state.original,
     state.current,
-    editMask.data,
-    protectedMask.data,
+    run.editMask,
+    run.protectedMask,
     overlap
   );
-  state.lastManifest = buildManifest(editMask.data, protectedMask.data, state.qa);
+  run.qa = state.qa;
+  run.manifest = buildManifest(run, state.qa);
+  state.lastManifest = run.manifest;
   renderQa(state.qa);
   setButtons(true);
 }
@@ -543,13 +656,39 @@
   $('compareLine').style.left = `${value}%`;
 }
 
+function restoreMaskAlpha(ctx, alphaData) {
+  const image = ctx.createImageData(ctx.canvas.width, ctx.canvas.height);
+  const isProtected = ctx === protectedCtx;
+  for (let i = 0; i < alphaData.length; i += 4) {
+    const alpha = alphaData[i + 3];
+    image.data[i] = isProtected ? 255 : 200;
+    image.data[i + 1] = isProtected ? 110 : 255;
+    image.data[i + 2] = isProtected ? 110 : 114;
+    image.data[i + 3] = alpha;
+  }
+  ctx.putImageData(image, 0, 0);
+}
+
 function undo() {
   if (!state.history.length) return;
-  state.current = state.history.pop();
+  const snapshot = state.history.pop();
+  state.current = snapshot.image;
+  state.activeRun = snapshot.run;
+  state.qa = snapshot.run?.qa || null;
+  state.lastManifest = snapshot.run?.manifest || null;
+
+  if (state.activeRun) {
+    restoreMaskAlpha(maskCtx, state.activeRun.editMask);
+    restoreMaskAlpha(protectedCtx, state.activeRun.protectedMask);
+    state.maskRevision = state.activeRun.maskRevision;
+    $('visualQa').value = state.qa?.visual_qa || 'PENDING';
+  }
+
   renderCurrent();
   renderComparison();
+  renderQa(state.qa);
   setButtons(true);
-  setStatus('已切回上一個候選結果；下一次生成仍會從最初原圖開始。');
+  setStatus('已切回上一個候選與其遮罩／QA 快照。');
 }
 
 function reset() {
@@ -559,6 +698,10 @@
   state.history = [];
   state.lastManifest = null;
   state.qa = null;
+  state.activeRun = null;
+  state.maskRevision = 0;
+  state.runSequence = 0;
+  state.lastPoint = null;
   renderCurrent();
   clearMasks();
   $('resultSection').classList.add('hidden');
@@ -569,15 +712,17 @@
 }
 
 function downloadImage() {
-  if (!state.current) return;
+  if (!state.current || !state.activeRun) return;
 
   const canvas = document.createElement('canvas');
   canvas.width = state.current.width;
   canvas.height = state.current.height;
   canvas.getContext('2d').putImageData(state.current, 0, 0);
 
+  const suffix = state.qa?.overall === 'PASS' ? 'validated' : 'candidate';
+  const runId = state.activeRun.manifest?.run_id || `run-${state.activeRun.runSequence}`;
   const link = document.createElement('a');
-  link.download = `ai-space-generator-${Date.now()}.png`;
+  link.download = `${runId}-${suffix}.png`;
   link.href = canvas.toDataURL('image/png');
   link.click();
 }
@@ -625,15 +770,15 @@
 $('brushSize').addEventListener('input', (event) => $('brushSizeValue').value = event.target.value);
 $('clearEditMaskBtn').addEventListener('click', () => {
   clearEditMask();
-  setStatus('Edit Mask 已清除。');
+  noteMaskRevision('Edit Mask 已清除。');
 });
 $('clearProtectedMaskBtn').addEventListener('click', () => {
   clearProtectedMask();
-  setStatus('Protected Mask 已清除。');
+  noteMaskRevision('Protected Mask 已清除。');
 });
 $('clearMaskBtn').addEventListener('click', () => {
   clearMasks();
-  setStatus('所有遮罩已清除。');
+  noteMaskRevision('所有遮罩已清除。');
 });
 $('generateBtn').addEventListener('click', generate);
 $('undoBtn').addEventListener('click', undo);
@@ -654,25 +799,10 @@
   });
 });
 
-['pointerdown', 'touchstart'].forEach((name) => {
-  maskCanvas.addEventListener(
-    name,
-    (event) => {
-      state.drawing = true;
-      drawMask(event);
-    },
-    { passive: false }
-  );
-});
-
-['pointermove', 'touchmove'].forEach((name) => {
-  maskCanvas.addEventListener(name, drawMask, { passive: false });
-});
-
-['pointerup', 'pointercancel', 'pointerleave', 'touchend', 'touchcancel'].forEach((name) => {
-  maskCanvas.addEventListener(name, () => {
-    state.drawing = false;
-  });
+maskCanvas.addEventListener('pointerdown', beginMaskStroke, { passive: false });
+maskCanvas.addEventListener('pointermove', continueMaskStroke, { passive: false });
+['pointerup', 'pointercancel', 'pointerleave'].forEach((name) => {
+  maskCanvas.addEventListener(name, endMaskStroke, { passive: false });
 });
 
 window.addEventListener('beforeinstallprompt', (event) => {
--- v0.2/index.html
+++ v0.3/index.html
@@ -14,7 +14,7 @@
 <body>
   <header class="topbar">
     <div>
-      <p class="eyebrow">AI SPACE GENERATOR · v0.2</p>
+      <p class="eyebrow">AI SPACE GENERATOR · v0.3</p>
       <h1>AI 空間生成器</h1>
     </div>
     <button id="installBtn" class="ghost hidden" type="button">安裝</button>
@@ -22,8 +22,8 @@
 
   <main class="app-shell">
     <section class="card intro">
-      <p>每一個候選版本都直接從最初原圖建立；Edit Mask 以外與 Protected Mask 內的像素會進行獨立驗證。</p>
-      <p class="notice">目前仍是本地端概念工具；Visual QA 必須由使用者確認，不能用 Pixel QA 取代。</p>
+      <p>每一個候選版本都直接從最初原圖建立；每次執行會凍結遮罩快照，Edit Mask 以外與 Protected Mask 內的像素獨立驗證。</p>
+      <p class="notice">目前仍是本地端概念工具；每個新候選會自動重設 Visual QA，不能沿用上一輪 PASS，也不能用 Pixel QA 取代。</p>
     </section>
 
     <section class="card controls">
@@ -121,6 +121,7 @@
       </label>
 
       <div id="qaResults" class="qa-grid"></div>
+      <p class="qa-note">遮罩在候選建立後仍可繼續修改；現有候選與 QA 永遠使用建立當下的凍結遮罩快照。</p>
 
       <div class="download-grid">
         <button id="downloadManifestBtn" type="button" disabled>下載 Manifest JSON</button>
@@ -151,7 +152,7 @@
     <section class="card roadmap">
       <p class="eyebrow">BOUNDARY</p>
       <h2>目前定位</h2>
-      <p>此版本完成 Source Lock、雙遮罩、原圖重跑、Pixel QA 與 Run Manifest。高品質生成式局部修補仍應由可驗證的外部編修能力處理，並回到同一套 QA Gate 驗證。</p>
+      <p>此版本完成 Source Lock、雙遮罩、原圖重跑、凍結 Run Snapshot、Pixel QA 與 Run Manifest。高品質生成式局部修補仍應由可驗證的外部編修能力處理，並回到同一套 QA Gate 驗證。</p>
     </section>
   </main>
 
--- v0.2/styles.css
+++ v0.3/styles.css
@@ -186,3 +186,5 @@
   .controls { display: grid; grid-template-columns: 1fr 1fr; gap: 14px 18px; }
   .upload-button, .meta-grid, .toolbar, .range-row { grid-column: 1 / -1; }
 }
+
+.qa-note { margin: 12px 0 0; color: var(--muted); font-size: .8rem; line-height: 1.5; }
```
