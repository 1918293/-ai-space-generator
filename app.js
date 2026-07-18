'use strict';

const $ = (id) => document.getElementById(id);
const state = {
  original: null,
  current: null,
  history: [],
  tool: 'brush',
  drawing: false,
  scale: 1,
  deferredInstall: null,
};

const imageInput = $('imageInput');
const photoCanvas = $('photoCanvas');
const maskCanvas = $('maskCanvas');
const beforeCanvas = $('beforeCanvas');
const afterCanvas = $('afterCanvas');
const photoCtx = photoCanvas.getContext('2d', { willReadFrequently: true });
const maskCtx = maskCanvas.getContext('2d', { willReadFrequently: true });
const beforeCtx = beforeCanvas.getContext('2d');
const afterCtx = afterCanvas.getContext('2d');

function setStatus(message, error = false) {
  $('status').textContent = message;
  $('status').style.color = error ? 'var(--danger)' : '';
}

function setButtons(enabled) {
  $('generateBtn').disabled = !enabled;
  $('resetBtn').disabled = !enabled;
  $('undoBtn').disabled = state.history.length === 0;
}

function fitDimensions(width, height, max = 1600) {
  const ratio = Math.min(1, max / Math.max(width, height));
  return { width: Math.round(width * ratio), height: Math.round(height * ratio) };
}

async function loadFile(file) {
  if (!file || !file.type.startsWith('image/')) {
    setStatus('請選擇有效的圖片檔案。', true);
    return;
  }

  const url = URL.createObjectURL(file);
  const img = new Image();
  img.onload = () => {
    const fitted = fitDimensions(img.naturalWidth, img.naturalHeight);
    [photoCanvas, maskCanvas, beforeCanvas, afterCanvas].forEach((canvas) => {
      canvas.width = fitted.width;
      canvas.height = fitted.height;
    });

    photoCtx.drawImage(img, 0, 0, fitted.width, fitted.height);
    state.original = photoCtx.getImageData(0, 0, fitted.width, fitted.height);
    state.current = new ImageData(new Uint8ClampedArray(state.original.data), fitted.width, fitted.height);
    state.history = [];
    clearMask();
    renderCurrent();
    renderMeta(file, img, fitted);
    $('emptyState').classList.add('hidden');
    $('canvasWrap').classList.remove('hidden');
    $('resultSection').classList.add('hidden');
    setButtons(true);
    setStatus('照片已載入。請塗選需要修改的區域。');
    URL.revokeObjectURL(url);
  };
  img.onerror = () => {
    URL.revokeObjectURL(url);
    setStatus('無法讀取這張圖片。', true);
  };
  img.src = url;
}

function renderMeta(file, img, fitted) {
  const kb = Math.round(file.size / 1024).toLocaleString('zh-TW');
  const modified = new Date(file.lastModified).toLocaleString('zh-TW');
  $('fileMeta').innerHTML = `
    <span>檔名：${escapeHtml(file.name)}</span>
    <span>大小：${kb} KB</span>
    <span>原始：${img.naturalWidth} × ${img.naturalHeight}</span>
    <span>處理：${fitted.width} × ${fitted.height}</span>
    <span>格式：${escapeHtml(file.type || '未知')}</span>
    <span>修改：${escapeHtml(modified)}</span>
  `;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (char) => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;' }[char]));
}

function renderCurrent() {
  if (!state.current) return;
  photoCtx.putImageData(state.current, 0, 0);
}

function clearMask() {
  maskCtx.clearRect(0, 0, maskCanvas.width, maskCanvas.height);
}

function canvasPoint(event) {
  const rect = maskCanvas.getBoundingClientRect();
  const source = event.touches ? event.touches[0] : event;
  return {
    x: (source.clientX - rect.left) * (maskCanvas.width / rect.width),
    y: (source.clientY - rect.top) * (maskCanvas.height / rect.height),
  };
}

function drawMask(event) {
  if (!state.drawing || !state.current) return;
  event.preventDefault();
  const { x, y } = canvasPoint(event);
  const size = Number($('brushSize').value);
  maskCtx.save();
  maskCtx.globalCompositeOperation = state.tool === 'eraser' ? 'destination-out' : 'source-over';
  maskCtx.fillStyle = 'rgba(200,255,114,.58)';
  maskCtx.beginPath();
  maskCtx.arc(x, y, size / 2, 0, Math.PI * 2);
  maskCtx.fill();
  maskCtx.restore();
}

function hasMask(maskData) {
  for (let i = 3; i < maskData.length; i += 4) if (maskData[i] > 15) return true;
  return false;
}

function saveHistory() {
  state.history.push(new ImageData(new Uint8ClampedArray(state.current.data), state.current.width, state.current.height));
  if (state.history.length > 12) state.history.shift();
}

function generate() {
  if (!state.current) return;
  const mask = maskCtx.getImageData(0, 0, maskCanvas.width, maskCanvas.height);
  if (!hasMask(mask.data)) {
    setStatus('請先用筆刷塗選要修改的區域。', true);
    return;
  }

  saveHistory();
  const output = new ImageData(new Uint8ClampedArray(state.current.data), state.current.width, state.current.height);
  const operation = $('operation').value;
  const color = hexToRgb($('fillColor').value);
  const width = output.width;
  const height = output.height;
  const source = state.current.data;
  const data = output.data;

  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const i = (y * width + x) * 4;
      const alpha = mask.data[i + 3] / 255;
      if (alpha <= 0.04) continue;
      const strength = Math.min(.92, alpha * .92);
      let target;

      if (operation === 'remove') {
        target = neighborhoodAverage(source, width, height, x, y, 20);
      } else if (operation === 'color') {
        const lum = (source[i] + source[i+1] + source[i+2]) / (255 * 3);
        target = [color.r * (.55 + lum * .55), color.g * (.55 + lum * .55), color.b * (.55 + lum * .55)];
      } else if (operation === 'wood') {
        const grain = 22 * Math.sin(y * .18 + Math.sin(x * .03)) + 10 * Math.sin(y * .035);
        target = [161 + grain, 116 + grain * .55, 73 + grain * .25];
      } else if (operation === 'stone') {
        const noise = ((x * 17 + y * 31) % 29) - 14;
        const vein = Math.abs(Math.sin(x * .018 + y * .027)) > .965 ? -45 : 0;
        target = [176 + noise + vein, 178 + noise + vein, 174 + noise + vein];
      } else if (operation === 'fabric') {
        const weave = ((x % 7 < 2) || (y % 7 < 2)) ? -26 : 8;
        target = [154 + weave, 144 + weave, 132 + weave];
      } else {
        target = [Math.min(255, source[i] * 1.28 + 18), Math.min(255, source[i+1] * 1.25 + 18), Math.min(255, source[i+2] * 1.18 + 14)];
      }

      data[i] = mix(source[i], target[0], strength);
      data[i+1] = mix(source[i+1], target[1], strength);
      data[i+2] = mix(source[i+2], target[2], strength);
    }
  }

  state.current = output;
  renderCurrent();
  clearMask();
  renderComparison();
  setButtons(true);
  const prompt = $('prompt').value.trim();
  setStatus(prompt ? `已產生概念模擬：「${prompt}」` : '已產生概念模擬。');
}

function neighborhoodAverage(data, width, height, x, y, radius) {
  const samples = [[-radius,0],[radius,0],[0,-radius],[0,radius],[-radius,-radius],[radius,radius]];
  let r = 0, g = 0, b = 0, count = 0;
  for (const [dx,dy] of samples) {
    const sx = Math.max(0, Math.min(width - 1, x + dx));
    const sy = Math.max(0, Math.min(height - 1, y + dy));
    const j = (sy * width + sx) * 4;
    r += data[j]; g += data[j+1]; b += data[j+2]; count++;
  }
  return [r/count, g/count, b/count];
}

function mix(a, b, t) { return Math.round(a * (1 - t) + b * t); }
function hexToRgb(hex) {
  const value = parseInt(hex.replace('#',''), 16);
  return { r: (value >> 16) & 255, g: (value >> 8) & 255, b: value & 255 };
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

function undo() {
  if (!state.history.length) return;
  state.current = state.history.pop();
  renderCurrent();
  renderComparison();
  setButtons(true);
  setStatus('已復原上一個修改。');
}

function reset() {
  if (!state.original) return;
  state.current = new ImageData(new Uint8ClampedArray(state.original.data), state.original.width, state.original.height);
  state.history = [];
  renderCurrent();
  clearMask();
  $('resultSection').classList.add('hidden');
  setButtons(true);
  setStatus('已回到原始照片。');
}

function download() {
  if (!state.current) return;
  const canvas = document.createElement('canvas');
  canvas.width = state.current.width;
  canvas.height = state.current.height;
  canvas.getContext('2d').putImageData(state.current, 0, 0);
  const link = document.createElement('a');
  link.download = `ai-space-generator-${Date.now()}.png`;
  link.href = canvas.toDataURL('image/png');
  link.click();
}

imageInput.addEventListener('change', (event) => loadFile(event.target.files?.[0]));
$('brushSize').addEventListener('input', (event) => $('brushSizeValue').value = event.target.value);
$('clearMaskBtn').addEventListener('click', () => { clearMask(); setStatus('選取區域已清除。'); });
$('generateBtn').addEventListener('click', generate);
$('undoBtn').addEventListener('click', undo);
$('resetBtn').addEventListener('click', reset);
$('downloadBtn').addEventListener('click', download);
$('compareRange').addEventListener('input', (event) => updateComparison(event.target.value));

document.querySelectorAll('[data-tool]').forEach((button) => {
  button.addEventListener('click', () => {
    state.tool = button.dataset.tool;
    document.querySelectorAll('[data-tool]').forEach((item) => item.classList.toggle('active', item === button));
  });
});

['pointerdown','touchstart'].forEach((name) => maskCanvas.addEventListener(name, (event) => { state.drawing = true; drawMask(event); }, { passive: false }));
['pointermove','touchmove'].forEach((name) => maskCanvas.addEventListener(name, drawMask, { passive: false }));
['pointerup','pointercancel','pointerleave','touchend','touchcancel'].forEach((name) => maskCanvas.addEventListener(name, () => { state.drawing = false; }));

window.addEventListener('beforeinstallprompt', (event) => {
  event.preventDefault();
  state.deferredInstall = event;
  $('installBtn').classList.remove('hidden');
});
$('installBtn').addEventListener('click', async () => {
  if (!state.deferredInstall) return;
  state.deferredInstall.prompt();
  await state.deferredInstall.userChoice;
  state.deferredInstall = null;
  $('installBtn').classList.add('hidden');
});

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('./sw.js').catch(() => {}));
}
