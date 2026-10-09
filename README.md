# Imaginarium Forge

Windows 本機優先的繁體中文創作工作室，整合自由草稿、角色設定、世界觀、故事寫作與圖片提示詞。作品儲存在本機，AI 候選由作者編輯、保存與接受，不會自動覆寫正式版本。

## 安裝與啟動

需要 Windows 10／11、網路連線，以及 [uv](https://docs.astral.sh/uv/)。首次安裝會下載 Python 3.12 與執行套件；AI 模型需另外設定。

1. 在 PowerShell 安裝 uv，完成後重新開啟 PowerShell：

   ```powershell
   winget install --id astral-sh.uv -e
   ```

2. 使用 Git 取得程式，或從 GitHub 下載並解壓縮原始碼：

   ```powershell
   git clone https://github.com/PXShou329/Imaginarium-Forge-Studio.git
   cd Imaginarium-Forge-Studio
   ```

3. 雙擊 **啟動 Imaginarium Forge.cmd**。啟動器會準備環境，並在瀏覽器開啟 `http://127.0.0.1:8525/`。

也可以在專案資料夾執行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\launch_local.ps1
```

若連接埠已被其他程式使用，指定另一個連接埠：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\launch_local.ps1 -Port 8526
```

## 開始創作

- **自由創作**：直接撰寫故事片段、角色與世界種子，不必先建立作品。
- **懶人標籤生成器**：選擇繁中標籤，組合角色、服裝、背景與畫面風格，再複製或下載英文 Prompt。
- **我的書架**：建立作品，管理角色、故事與設定。
- **故事寫作**：整理故事需求、大綱、章節與場景，保存草稿、接受版本、續寫或改編劇本。
- **正式提示詞工作台**：結合場景、角色與視覺風格，保存及匯出 Prompt 版本。

沒有 AI 模型或 API Key 也可以手寫、使用內建靈感素材、整理標籤與保存作品。詳細操作見 [創作指南](docs/guides/CREATIVE_WORKFLOWS.md)。

## 選用 AI 與翻譯

從頂部 **狀態 → AI 設定** 選擇服務與模型。設定完成後，按下 AI 動作才會發送請求。

- **Ollama**：自行安裝並啟動本機 Ollama，準備適合的文字生成模型。模型運行速度與可用長度取決於硬體和模型。
- **OpenAI**：需要可用的 API Key、帳戶額度與支援的模型。啟用後，生成所需內容會傳送至 OpenAI，並可能產生費用。請在介面中提供工作階段金鑰或使用作業系統憑證庫保存。
- **離線翻譯**：從頂部 **翻譯** 匯入相容的完整模型包。模型包需含 `manifest.json`、模型檔案與 SHA-256；權重不含在本倉庫。模型包通過完整性檢查後才能翻譯，品質取決於所安裝模型。
- **ComfyUI**：Prompt 可匯出到外部工具使用；本程式不會安裝或執行 ComfyUI。

API Key 不應寫進 `.env`、設定檔或原始碼。`.env.example` 僅提供非機密設定範例。

## 保存、備份與更新

作品預設儲存在 `data/`，包含本機資料庫與備份。原始碼不包含作品、金鑰或模型權重。

在 **設定與說明 → 設定與備份** 建立或還原備份。更新前先備份，關閉應用程式，再執行 `scripts/bootstrap.ps1` 更新鎖定環境。資料庫需要升級時，依畫面提示操作。

關閉瀏覽器不會停止背景服務。需完全停止時，結束該次 Python／Streamlit 程序；不要讓兩個程序同時操作同一份資料庫。搬移作品請使用備份功能，不要直接複製執行中的 SQLite 檔案。

生成紀錄可能包含完整故事與提示詞，請妥善保管資料庫及備份。[生成紀錄與隱私](docs/CONTEXT_INSPECTION.md) 說明保存內容與設定方式。

## 環境檢查與測試

環境診斷會檢查 Python、資料目錄、SQLite 與選用的 Ollama，不會下載模型：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_doctor.ps1
```

安裝完成後可執行離線測試；測試使用暫存資料，不呼叫生成模型：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
