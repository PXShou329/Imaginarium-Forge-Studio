# Imaginarium Forge｜你的本機故事書房

Imaginarium Forge 是一套 Windows 本機優先、以繁體中文操作的故事創作工作室。你可以從一句
靈感、一名角色、一段故事、世界觀或圖片 Prompt 開始，也可以打開一本作品，集中整理故事、
角色、Canon、視覺風格、版本與匯出內容。

這不是會自行改寫原稿的聊天機器人。AI、離線隨機與本機模型產生的內容都先成為候選；你可以
修改、比較、保存，再明確決定是否接受為正式版本。

## Windows 快速開始

### 第一次啟動

1. 將整個專案資料夾解壓縮到可寫入的本機位置；不要直接在 ZIP 裡執行。
2. 安裝 `uv`。Windows 10／11 可在 PowerShell 執行：

   ```powershell
   winget install --id astral-sh.uv -e
   ```

3. 安裝完成後重新開啟 PowerShell，讓 `uv` 進入 PATH。
4. 回到專案根目錄，雙擊 **`啟動 Imaginarium Forge.cmd`**。

第一次啟動會自動建立鎖定的 Python 環境並下載必要套件，完成後開啟：

```text
http://127.0.0.1:8525/
```

這一步需要網路，但不會安裝 Ollama、下載模型、安裝 ComfyUI 或傳送你的創作內容。

若第一次啟動顯示找不到 `uv`，請確認 `uv --version` 可在新開的 PowerShell 中執行；也可以在
專案根目錄手動執行：

```powershell
.\scripts\bootstrap.ps1
```

完成後再雙擊啟動檔。

### 之後每次開啟

直接雙擊 **`啟動 Imaginarium Forge.cmd`**。啟動器會等待本機工作室就緒，再自動打開瀏覽器；
如果服務已經在執行，只會重新開啟現有工作室，不會重複啟動。

關閉瀏覽器分頁不一定會停止背景中的本機服務；這不會把內容上傳到網路。若需要完全停止，請
結束該次 Imaginarium Forge 的 Python／Streamlit 程序。

## 介面導覽

新版 AppShell 保留書房、書架與打開一本書的概念，同時讓常用功能更容易找到。

- **左側導覽**：首頁、自由創作、我的書架、創作工作室、AI 與工具、設定與說明。
- **上方工具列**：返回剛才使用的頁面、依工作頁順序前後移動、搜尋工具、快速創作，以及查看
  本機環境與 AI 設定狀態。
- **返回**依目前工作階段的實際瀏覽紀錄移動；**上一頁／下一頁**依應用程式的固定工作頁順序
  移動，不等同瀏覽器上一頁。
- 圖片提示詞草稿有未保存內容時，任何應用內導覽仍會先經過保存保護，不會直接丟失內容。

### 首頁

首頁會顯示目前作品、最近編輯的書、真實存在的內容摘要，以及角色、故事、世界與 Prompt 的
快速入口。空書架不會填入示範作品；你可以建立第一本書，也可以先進入自由創作。

### 我的書架

每一本書都是獨立作品。你可以建立、搜尋、開啟、修改書名與簡介、暫時封存或重新放回書架。
封存不會刪除內容；目前沒有雲端同步或多人共編。

### 自由創作

不必先建立作品即可使用：

- **自由創作箱**：集中查看已正式保存、尚未綁定作品的草稿。
- **懶人標籤生成器**：用繁體中文標籤組合角色、背景與 Negative Prompt；不需 API Key。
- **故事片段草稿**：先寫敘事、對白或開場，再決定是否加入作品。
- **角色與故事草稿**：整理角色資料、個人故事與圖片提示詞。
- **世界種子草稿**：保存世界規則、地點、社會、科技／魔法、衝突與主題。
- **圖片提示詞草稿**：保存、精修與恢復 character／background Prompt。

故事片段與世界種子採明確保存；圖片提示詞草稿另有本機 autosave、dirty guard 與重新啟動後
的 recovery。自由草稿不會靜默變成正式角色、Story Scene、Story Bible 或 Canon。

### 創作工作室

打開作品後，可在同一作品脈絡中使用：

- **Creative Launchpad**：從想法建立角色、世界、故事基礎與視覺提示詞，再分別保存。
- **靈感房**：以離線素材、Ollama 或 OpenAI 產生多個候選，鎖定喜歡的部分後重抽或混合。
- **Canon Vault**：查看作品的 Canon 概況，並前往正式角色、視覺風格與 Story Bible。
- **角色**：管理角色資料、來源、角色弧、Visual DNA 版本與服裝。
- **Style Profiles**：保存可重用的視覺風格與不可變版本。
- **Prompt Studio**：解析場景、選定精確角色／風格／模型版本、編譯、檢查並匯出正式 Prompt。
- **Story Studio**：管理需求、Story Bible、大綱、章節、Scene Card、生成與修訂、Story Memory、
  Context Inspector、匯出與 screenplay adaptation。

Canon Vault 目前是作品概況與入口，不是泛用百科資料庫；主要 Story Bible 與世界規則仍在
Story Studio 中管理。

## AI 候選與版本保存

Imaginarium Forge 的核心規則是：**AI 不得靜默覆寫作者正式內容**。

1. 你先選擇離線、本機 Ollama 或 OpenAI，並提供線索或既有上下文。
2. 系統產生候選；候選可被比較、重抽或編輯。
3. 保存編輯內容會建立新版本，而不是改寫舊版本。
4. 只有你明確接受後，正式版本指標才會移動。

Story Memory 也必須經過接受才會成為後續上下文。Screenplay adaptation 擁有自己的版本鏈，
不會覆寫原小說 Scene。成人輸出另有明確審閱與接受流程。

## Ollama、OpenAI 與 ComfyUI 的真實邊界

### 不使用任何模型

自由輸入、內建離線隨機、懶人標籤、草稿管理、版本、備份與大部分編輯功能都不需要模型或
API Key。

### Ollama

- Ollama 是獨立的本機工具，需由你自行安裝並啟動；Imaginarium Forge 不會安裝或啟動它。
- 預設只連到 `http://localhost:11434`。
- 若所選模型尚未存在，介面會詢問是否下載；只有按「是」才會要求 Ollama 下載，按「否」不會
  進行下載或生成。
- 模型大小、速度與品質取決於模型、Ollama 版本及電腦的 RAM／VRAM。
- 如果你自行把 Ollama endpoint 改成遠端位址，傳送的創作內容就會離開本機。

### OpenAI API

- OpenAI 是選用的遠端付費服務；是否可用、費用與額度由你的 OpenAI 帳戶決定。
- 在「AI 與工具 → AI 設定」輸入 Key，並可選擇是否在**目前工作階段**記住。
- 「記住」不等於永久保存，也不等於同意傳送內容；每次真正呼叫前仍需明確同意。
- 使用 OpenAI 時，該次請求所需的故事、角色、Prompt 或上下文會傳到 OpenAI。
- Key 或帳戶有問題時，介面會區分常見的驗證、額度／計費、速率、模型、逾時、連線與回應格式
  錯誤，但不會為排錯偷偷再發一次請求。

### ComfyUI

ComfyUI 永遠是外部工具。Imaginarium Forge 只準備、版本化與匯出圖片／影片 Prompt；目前不會：

- 安裝或啟動 ComfyUI；
- 下載 ComfyUI 模型；
- 呼叫 ComfyUI API 或送出 queue；
- 在專案內生成圖片、GIF 或影片。

請將匯出的 Prompt 自行帶到你的地端 ComfyUI 使用。

## API Key 安全

手動貼入的 OpenAI API Key 只留在目前 Streamlit 工作階段的記憶體。它不應寫入：

- SQLite；
- `.env`；
- 備份；
- Prompt 或匯出檔；
- log、文件、截圖或 Git。

不要把 Key 貼進作品內容或任何要送給模型的欄位。關閉工作階段後，請在下次需要時重新輸入。

## 本機資料與備份

| 內容 | 預設位置 |
|---|---|
| 作品、角色、故事、版本與 Story Memory | `data\imaginarium_forge.db` |
| Prompt Scratch recovery journal | 同一個 SQLite 資料庫 |
| 應用程式建立的備份 | `data\backups\` |
| 本機 Python 環境 | `.venv\` |
| Ollama 模型 | Ollama 自己的模型目錄 |

請在「設定與說明 → 設定與備份」使用內建備份功能。它會使用 SQLite online backup，能正確納入
WAL 中尚未整理的資料。搬到另一台電腦時，請一起保存備份檔與同名 manifest；不要直接複製仍在
運作中的主資料庫，也不要讓兩台電腦同時開啟同一個放在 OneDrive、Dropbox、NAS 或網路磁碟的
SQLite 檔案。

資料庫、備份、匯出與 Context Inspector 都可能包含完整私人原稿、成人內容及曾傳給 provider
的文字，請視為私密資料保管。

## 已知產品限制

- 目前是單一使用者、本機 Streamlit 應用程式；沒有帳號、雲端同步、多人協作或兩台電腦資料庫
  自動合併。
- React／Tauri 專業桌面介面仍採漸進遷移；目前介面不代表最終桌面版。
- Prompt Scratch 已有 autosave／dirty recovery；Story Fragment、World Seed 與其他編輯器尚未
  全面套用同等級保護，離開前請明確保存。
- Stable Story Block IDs 已存在於後端版本資料，但目前沒有 block editor、beat anchor 或媒體綁定
  介面。
- Story Fragment 尚不能一鍵轉成 Story Studio Scene；世界種子也不會自動成為 Story Bible。
- Screenplay adaptation 已有獨立版本，但 Shot List、Storyboard、Fountain、完整影片 Prompt
  package 與 block-level adaptation 尚未提供。
- 尚未提供媒體收件匣、圖片回填、段落／角色／分鏡媒體綁定或完整圖文故事編排。
- 沒有通用 hard delete 與全站 Undo／Redo；請使用版本、封存與備份保留可恢復性。
- 本機模型與 OpenAI 的品質、速度、輸出長度與費用不由本應用程式保證。

## 使用指南

- [創作工作流指南](docs/guides/CREATIVE_WORKFLOWS.md)
- [Context Inspector 與私密文本說明](docs/CONTEXT_INSPECTION.md)
