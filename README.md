# Hao System

> 一套以 **可持續性（Sustainability）** 為核心理念的人機協作系統。

---

# 專案介紹

Hao System 致力於建立一套長期可維護、可擴充且可持續發展的知識管理與 AI 協作架構。

本專案結合：

- AI 協作
- 知識管理
- 程式開發
- 專案管理
- 文件管理
- 自動化流程

打造一個能夠隨時間持續成長，而非持續累積混亂的工作系統。

---

# 專案目標

本專案以以下五項核心目標為基礎：

- 建立 Single Source of Truth（唯一真實來源）
- 降低資訊遺失與重複工作
- 保留完整決策脈絡
- 提升 AI 與人類協作效率
- 建立可長期維護的系統架構

---

# 系統架構

## Google Drive

負責保存：

- Manifest
- Master Index
- Portal
- 正式文件
- 專案規格
- 決策紀錄

---

## GitHub

負責管理：

- 程式碼
- Script
- 設定檔
- Issue
- Pull Request
- Release
- 版本控制

---

## AI（ChatGPT / Codex / Copilot）

協助：

- 分析需求
- 撰寫程式
- 改善架構
- 產生文件
- 問題排除
- 工作流程優化

---

# Repository 原則

本 Repository 主要保存：

- 原始碼
- 前端
- 後端
- API
- 自動化腳本
- 開發文件

正式知識文件仍以 Google Drive 為唯一正式來源。

---

# AI 協作

本 Repository 已包含：

- `AGENTS.md`

所有 AI Agent 在執行工作前，應優先閱讀 `AGENTS.md`，並遵循其中定義的工作規範。

---

# Challenger Lane

新的影像模型、編修工具、分析方法或工具版本，不直接取代目前已驗證流程；先以 Challenger 身分進行可比較測試。

Challenger 測試必須遵守：

- 使用與 Baseline 相同的 Original / Source Lock。
- 不使用前一輪生成圖、A/B 圖或衍生圖作為新的分析或編修來源。
- 只改變待測工具或待測方法，避免混入無法歸因的額外變更。
- 使用與 Baseline 相同且符合任務類型的 Pixel / Geometry / Identity / Visual QA。
- A/B 與 regression 指標在 Version Gate 前完成驗證；數值改善不能取代 Visual 或 Identity QA。
- 工具成功輸出不等於品質提升；只有證據顯示至少一項重要指標實質改善，且既有高價值指標沒有退化時，才可升格。

Challenger 狀態只使用：

- `PROMOTED`：通過必要 QA 與 Version Gate，可進入主流程。
- `CANDIDATE`：有價值，但證據或品質尚不足。
- `REJECTED`：相較 Baseline 退化。
- `BLOCKED`：受工具、權限或必要資料限制，無法完成有效比較。

GitHub 保存可重現的 Workflow、Validator、Config、Schema、Tests 與 Eval；Data Analytics 在 Version Gate 前負責 A/B、regression 與數據可信度檢查。Adobe、Picsart 或其他執行工具只作為 Execution Adapter，不各自定義另一套正式版本判定規則。

---

# 開發原則

我們遵循以下原則：

- 可持續性優先
- 簡潔優先
- 可維護優先
- 可回復優先
- 文件與程式同步更新

避免：

- 重複程式碼
- 過度設計
- 不必要的複雜度
- 難以維護的架構

---

# 專案狀態

目前階段：

- Repository 已建立
- AGENTS.md 已建立
- README 已建立
- GitHub 工作流程建置中

---

# License

本專案目前僅供 Hao System 內部開發與研究使用。