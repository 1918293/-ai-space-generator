# AGENTS.md

# Hao Codex Workspace AI 協作規則

本檔案定義此 Repository 中所有 AI（ChatGPT、Codex、GitHub Copilot 等）的共同工作原則。

---

## 專案目的

本 Repository 作為 Hao 系統的程式開發工作區。

正式控制文件仍以 Google Drive（Manifest、Master Index、Portal）為準。

GitHub 僅負責：

- 程式碼
- 設定檔
- Script
- Issue
- Pull Request
- 版本控制
- 測試

---

## 工作原則

AI 在執行任何工作時，依照以下順序：

1. 理解使用者真正目的
2. 優先維護可持續性
3. 優先修改既有內容
4. 避免新增不必要檔案
5. 保持最小可驗證修改
6. 保持可回復（Reversible）

---

## 修改原則

優先順序：

1. 修正
2. 合併
3. 簡化
4. 標準化
5. 自動化
6. 最後才新增

避免：

- 重複程式
- 重複文件
- 不必要架構
- 過度抽象
- 無用途模組

---

## Commit 原則

每一次 Commit 僅包含一個邏輯修改。

Commit Message 使用簡潔英文，例如：

- Add AGENTS.md
- Fix login flow
- Update README
- Refactor API service

避免一次 Commit 多個不同目的。

---

## AI 執行限制

未取得使用者明確同意前，不得：

- 刪除大量檔案
- 修改 Repository 結構
- 修改 GitHub Secrets
- 修改 GitHub Actions
- 推送到 Production
- 建立付費服務
- 上傳敏感資料

---

## AI 回覆格式

執行工作時，盡量提供：

1. 目前狀態
2. 已完成內容
3. 驗證結果
4. 風險
5. 唯一下一步

避免一次執行過多工作。

---

## Repository 原則

Google Drive：

- 正式文件
- Manifest
- 決策
- 規格

GitHub：

- 程式碼
- Script
- Issue
- Pull Request
- 測試
- Release

避免將 Google Drive 作為 GitHub 備份。

---

## AI 最終原則

任何修改都必須：

- 可以理解
- 可以回復
- 可以維護
- 可以驗證
- 可以長期持續使用
