---
title: AI Space Generator
emoji: 🏠
colorFrom: gray
colorTo: indigo
sdk: gradio
sdk_version: 6.5.1
app_file: app.py
pinned: false
license: mit
---

# AI 空間生成器

從現況照片讀取拍攝資訊、分析影像、塗選需要修改的區域，生成空間改造概念示意並下載前後結果。

> 目前是照片工作流 MVP。結果只適合設計討論，不是施工圖、測量結果、結構判斷或精確 3D 模型。

## 已完成

- 現況照片上傳與手機拍攝
- EXIF 日期、時間、裝置和 GPS 讀取
- 離線影像特徵與主要色彩分析
- 選配 Hugging Face 物件偵測
- 筆刷區域選取
- 本機物件移除、材質與顏色示意
- 選配 Hugging Face image-to-image 生成
- 未選取區域保留
- 修改前後比較
- PNG 下載
- 無帳號、單次工作階段

## 快速執行

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
python app.py
```

開啟終端機顯示的本機網址，通常是 `http://127.0.0.1:7860`。

## iPhone 本機人像（Draw Things）

`draw-things-portrait.js` 是 Draw Things 的本機人像工作流。它不使用本專案的 Hugging Face 遠端生成路徑，也不按張消耗雲端生成額度。

工作流會：

1. 在 Draw Things 內要求選取一張成人參考照片。
2. 自動下載並使用 `Realistic Vision v5.1 (8-bit)` 與 `IP Adapter Plus Face (SD v1.x)`。
3. 以 512 × 768、單張輸出執行本機生成。
4. 以 IP Adapter Face 控制人物辨識度，預設強度 0.74，可在 0.55–0.90 間調整。
5. 生成完整頭到腳、自然比例、低 AI 感的寫實人像，並存入 Draw Things 可用的 Pictures 位置。

參考照片只在 Draw Things 的本機工作流中選取；本 Repository 不保存或提交參考照片。

使用方式：

1. 在 Draw Things 的 Scripts 功能建立或匯入 `draw-things-portrait.js`。
2. 執行腳本。
3. 選取參考照片。
4. 保持預設 Identity strength，或依需要調整。
5. 按下 Generate。第一次執行會先下載必要模型，後續可直接在裝置上重複生成。

## 啟用遠端 AI

複製環境變數範例：

```bash
cp .env.example .env
```

設定 `HF_TOKEN` 後，在執行環境中載入該變數。Hugging Face Spaces 請把 token 設為 Space Secret，不要提交到 GitHub。

可選環境變數：

- `DETECTION_MODEL`：物件偵測模型 ID
- `GENERATION_MODEL`：image-to-image 模型 ID

未設定 token 時，工具仍可使用本機示意模式；介面會明確標記它不是生成式 AI 結果。

## 部署到 Hugging Face Spaces

1. 建立新的 Gradio Space。
2. 將此 Repository 的全部檔案推送到 Space。
3. 在 Space Settings → Secrets 新增 `HF_TOKEN`（需要遠端 AI 時）。
4. 等待依賴安裝及建置完成。

此 Repository 根目錄的 README YAML 已包含 Gradio Space 設定。

## Docker

```bash
docker build -t ai-space-generator .
docker run --rm -p 7860:7860 --env-file .env ai-space-generator
```

## 使用流程

1. 上傳或拍攝現況照片。
2. 執行照片分析，確認拍攝資訊與辨識結果。
3. 把照片載入編輯器。
4. 用筆刷塗選要修改的物件或表面。
5. 選擇移除、替換材質或更換顏色。
6. 輸入設計要求並生成。
7. 比較修改前後並下載 PNG。

## 專案結構

```text
ai-space-generator/
├── app.py
├── draw-things-portrait.js
├── src/core.py
├── tests/test_core.py
├── requirements.txt
├── Dockerfile
├── .env.example
└── .github/workflows/test.yml
```

## 後續路線

- 室內專用語意分割與物件遮罩
- 更準確的局部生成與幾何保持
- 2D 平面圖辨識
- 平面圖轉概念性 3D
- 多視角一致生成
- glTF／Web 3D 匯出

## 隱私

程式本身不建立帳號或資料庫。部署平台可能保留請求日誌或暫存檔，實際公開前應依平台政策補充隱私聲明。分享輸出前，請確認是否需要移除定位與 EXIF 資訊。
