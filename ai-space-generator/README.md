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

## 人像生成工作流：目前收斂結論（2026-09-27）

本段整理本次「同一成人女性＋指定深酒紅泳裝＋全身寫實人像」實測所得，作為後續避免重複探索的開發紀錄。

### 需求邊界

- 優先維持人物辨識度、臉部比例、酒紅長髮與自然膚質。
- 泳裝以參考照中可見的深酒紅色、中央抓皺／結飾、簡潔剪裁為設計基準。
- 全身需完整到腳，姿勢健康自然、非性感化，避免誇張身形、塑膠皮膚、AI 髮絲與手腳解剖錯誤。
- 若採 outpaint／局部生成，原始保護區像素應回貼驗證；生成區與原圖區需明確分離。

### 已驗證的開源候選

- Draw Things：iPhone／iPad 本機生成路徑，可搭配 Realistic Vision 與 IP Adapter Plus Face；不按張消耗雲端 API credit。
- PhotoMaker V2：適合人物身份保留，可與 IP-Adapter／ControlNet 組合。
- PuLID／PuLID-FLUX：人物 ID 保留候選。
- InstantID／InstantID-Rome：單張身份條件與構圖控制候選。
- DreamO：ID／Try-On／Multi Condition 候選。
- ComfyUI：適合桌面或持久 GPU runtime 的工作流編排。
- stable-diffusion.cpp：可用於本機／自架推論，支援 img2img、inpaint、IP-Adapter、PhotoMaker、PuLID 等相關能力。

### 架構結論

GitHub 應負責：

- 程式碼、workflow、版本控制與可重現設定。
- build、測試、驗證與結果紀錄。
- 不把一般 GitHub hosted CPU runner 當作常態影像推論伺服器。

實際影像推論應優先放在：

1. 手機本機 Draw Things；或
2. 可持久保存模型與 runtime 的 GPU 執行環境。

如此可避免每張圖重新下載模型、重新建立 runner、重做加密 handoff，也能避免按張 API credit。

### GitHub Actions 實驗結果

`.github/workflows/portrait-open-runtime.yml` 已證明以下步驟可以在 GitHub Actions 串起：

- 一次性公開金鑰與加密參考圖 handoff。
- runner 內解密參考圖。
- 建立 512 × 1024 outpaint proxy 與 mask。
- 下載 stable-diffusion.cpp 與 SD 1.5 inpainting GGUF。
- 原圖保護區回貼與 artifact 輸出流程。

但實測也確認：

- hosted `ubuntu-24.04` runner 為 CPU 路徑，512 × 1024、28 steps 的 diffusion 推論耗時高。
- 第一輪曾因等待 encrypted reference 超時。
- rerun 曾遇到 workflow 自行 push `main` 與遠端新 commit 的 non-fast-forward；後續已加入 fresh fetch/reset 與 push 失敗時 rebase 重試。
- 因此此 workflow 僅保留作實驗／驗證用途，不應作為日常人像生成的預設 runtime。

目前實驗 run `36260231385` 已通過參考圖 handoff、解密、outpaint 準備及 runtime/model 下載；最後一次 readback 時仍停留在 CPU diffusion 生成階段，尚未形成已驗證最終影像。

### 後續原則

- 不再優先測試有按張點數限制的生成平台。
- 不為同一任務反覆建立新的平行影像工作流。
- 優先沿用現有 Draw Things／stable-diffusion.cpp 能力。
- 真正需要遠端自動化時，先確認有可持久化的 GPU runtime，再把 GitHub 作為控制與版本層。
- 未取得明確授權前，不建立付費 GPU 資源。

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