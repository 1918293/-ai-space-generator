# AGENTS.md

# Hao System AI Collaboration Guide

> 本文件定義所有 AI Agent 在此 Repository 的共同工作規範。

---

# 專案定位

本 Repository 為 **Hao System** 的程式開發工作區。

正式知識來源（Single Source of Truth）如下：

- Google Drive：正式文件、Manifest、Master Index、Portal
- GitHub：程式碼、設定、Issue、版本控制
- ChatGPT：分析、規劃、協作與內容產生

AI 不應混淆上述三者的角色。

---

# 工作目標

AI 的首要任務：

1. 維護系統可持續性（Sustainability）
2. 保持架構簡潔
3. 降低維護成本
4. 保留完整決策脈絡
5. 協助使用者，而非取代使用者

---

# AI 工作原則

執行任何任務前：

1. 理解需求
2. 評估影響
3. 優先修改既有內容
4. 最後才新增檔案

每次修改皆應：

- 小幅修改
- 可驗證
- 可回復
- 可維護

---

# Repository 原則

優先修改：

- README
- 現有程式
- 現有模組

避免：

- 建立重複功能
- 建立重複文件
- 建立未使用檔案
- 過度設計
- 不必要抽象化

---

# Commit 原則

每次 Commit 僅做一件事。

Commit Message 使用英文，例如：

- Add AGENTS.md
- Update README
- Fix login flow
- Refactor API
- Improve documentation

避免：

- 一次修改大量功能
- 混合不同目的
- 無描述 Commit

---

# AI 限制

未取得使用者明確同意前，不得：

- 刪除大量檔案
- 修改 Repository 架構
- 修改 GitHub Secrets
- 修改 GitHub Actions
- 推送正式環境
- 建立付費服務
- 上傳敏感資料

---

# 回覆格式

AI 回覆建議包含：

## 目前狀態

目前專案狀態。

## 已完成

已完成事項。

## 建議

最佳做法。

## 風險

可能影響。

## 下一步

一次只提供一個最重要的下一步。

---

# Coding Style

程式碼應：

- 可閱讀
- 可維護
- 命名一致
- 避免重複
- 優先簡潔

優先修正問題，而非增加複雜度。

---

# Documentation

所有重要修改應同步更新：

- README
- 文件
- 註解（必要時）

避免文件與程式不同步。

---

# 最終原則

每一項修改都應符合：

- 易理解
- 易維護
- 易驗證
- 易回復
- 可長期持續使用

若有多種可行方案，優先選擇最簡單、最穩定、最容易維護的方案。