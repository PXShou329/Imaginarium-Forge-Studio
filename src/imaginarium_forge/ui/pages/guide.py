"""Friendly, project-optional guide to the Imaginarium Forge workbench."""

from __future__ import annotations

import streamlit as st

from imaginarium_forge.ui.components import creator_path_grid, page_header
from imaginarium_forge.ui.pages.character_drafts import PAGE_KEY as CHARACTER_DRAFTS_PAGE_KEY
from imaginarium_forge.ui.pages.free_creation_inbox import (
    PAGE_KEY as FREE_CREATION_INBOX_PAGE_KEY,
)
from imaginarium_forge.ui.pages.prompt_scratch import PAGE_KEY as PROMPT_SCRATCH_PAGE_KEY
from imaginarium_forge.ui.pages.prompt_tag_builder import (
    PAGE_KEY as PROMPT_TAG_BUILDER_PAGE_KEY,
)
from imaginarium_forge.ui.pages.story_fragment_drafts import (
    PAGE_KEY as STORY_FRAGMENT_DRAFTS_PAGE_KEY,
)

PAGE_KEY = "Guide"
PAGE_LABEL = "使用說明"

GUIDE_SECTION_TITLES = (
    "先選你現在想做的事",
    "兩種創作路徑",
    "AI、圖片與隱私",
    "功能快速查找",
    "資料與備份",
)


def _go(page: str) -> None:
    st.session_state["pending_nav"] = page
    st.rerun()


def _quick_start() -> None:
    st.markdown(f"## {GUIDE_SECTION_TITLES[0]}")
    creator_path_grid(
        (
            (
                "◇",
                "先寫一張自由草稿",
                "角色、故事片段、世界與圖片 Prompt 都能先獨立創作，不必先建立作品。",
            ),
            (
                "✦",
                "請 AI 或本機靈感補足",
                "留下少量線索，再選擇手動、離線隨機、本機 Ollama 或 OpenAI 繼續。",
            ),
            (
                "▤",
                "發展成完整作品",
                "從書架打開一本書，在同一個工作區整理角色、世界觀、故事與提示詞。",
            ),
        )
    )

    if st.button(
        "打開自由創作箱",
        key="guide_go_free_creation_inbox",
        type="primary",
        use_container_width=True,
    ):
        _go(FREE_CREATION_INBOX_PAGE_KEY)

    first_row = st.columns(3)
    if first_row[0].button(
        "用繁中標籤組英文 Prompt",
        key="guide_go_prompt_tag_builder",
        use_container_width=True,
    ):
        _go(PROMPT_TAG_BUILDER_PAGE_KEY)
    if first_row[1].button(
        "寫一段故事片段",
        key="guide_go_story_fragment_drafts",
        use_container_width=True,
    ):
        _go(STORY_FRAGMENT_DRAFTS_PAGE_KEY)
    if first_row[2].button(
        "建立角色草稿",
        key="guide_go_character_drafts",
        use_container_width=True,
    ):
        _go(CHARACTER_DRAFTS_PAGE_KEY)

    second_row = st.columns(3)
    if second_row[0].button(
        "自由撰寫圖片 Prompt",
        key="guide_go_prompt_scratch",
        use_container_width=True,
    ):
        _go(PROMPT_SCRATCH_PAGE_KEY)
    if second_row[1].button(
        "隨機產生整套創作靈感",
        key="guide_go_inspiration",
        use_container_width=True,
    ):
        _go("靈感房")
    if second_row[2].button(
        "管理作品書架",
        key="guide_go_projects",
        use_container_width=True,
    ):
        _go("專案")


def _creative_paths() -> None:
    st.markdown(f"## {GUIDE_SECTION_TITLES[1]}")
    standalone, project = st.columns(2)
    with standalone, st.container(border=True):
        st.markdown("### 不開作品，先自由創作")
        st.markdown(
            """
1. 到 **自由創作箱** 選擇故事片段、角色、世界或圖片 Prompt。
2. 自己編輯、離線隨機，或選擇可用的 AI 協助。
3. 明確保存後，草稿才會列在自由創作箱；未綁作品且未封存的內容都能再打開。
4. 準備好時，再由你決定是否把內容放進某一本作品。
            """
        )

    with project, st.container(border=True):
        st.markdown("### 打開一本書，繼續完整創作")
        st.markdown(
            """
1. 到 **作品書架** 建立作品，或打開既有作品。
2. 用 **完整創作與精修** 整理故事、角色與世界觀。
3. 到 **故事寫作**、**專案角色**、**世界觀設定**與**正式提示詞工作台**細修。
4. AI 只會產生候選；正式內容仍由你選擇、接受與保存。
            """
        )

    st.info(
        "圖片提示詞草稿會自動保存並提供復原；其他編輯頁請依畫面上的保存提示操作。"
    )


def _ai_and_privacy() -> None:
    st.markdown(f"## {GUIDE_SECTION_TITLES[2]}")
    st.markdown(
        """
- **完全手動或離線隨機**：不需要 API Key，選完仍可逐項修改。
- **懶人標籤生成器**：繁中標籤會立即更新下方英文 Prompt；切換分類不會清空選擇。
- **本機 Ollama**：只在你主動選用時執行；若模型尚未安裝，頁面會先詢問是否下載。
- **OpenAI**：先在 **AI 設定** 提供 Key；每次遠端生成前仍會再次取得你的同意。
- **ComfyUI**：本專案只準備 Prompt 與接收你匯回的媒體，不會安裝、啟動或呼叫 ComfyUI。
        """
    )
    st.warning(
        "OpenAI API Key 不會寫入作品、草稿、本機資料庫、備份、匯出檔、"
        "Prompt、執行紀錄或文件。"
    )

    with st.expander("進階標籤說明"):
        st.markdown(
            """
- 角色身分分成 **一般／奇幻**、**獸人／半獸人**與**福瑞**，三者不會同時混用。
- 「只補空白」會保留已選標籤；「全部重新隨機」會重抽，再讓你手動增減。
- 18+ 標籤預設關閉，只適用於年齡明確為 18 歲以上且所有互動均為合意的角色。
- 成人身體與親密動作需要相容服裝；若組合矛盾，頁面會保留上一個有效 Prompt 並提示調整。
- 「其他」親密行為只接受手動選擇，不會由隨機功能自動加入。
            """
        )


def _feature_map() -> None:
    st.markdown(f"## {GUIDE_SECTION_TITLES[3]}")

    with st.expander("自由創作箱", expanded=True):
        st.markdown(
            """
- **故事片段草稿**：獨立保存敘事、對白、場景或故事開場。
- **角色與故事草稿**：建立角色資料、個人故事與角色圖片 Prompt。
- **世界種子草稿**：保存世界規則、地點、衝突與氣氛。
- **懶人標籤生成器**：用繁中標籤組出全英文角色、背景與 Negative Prompt。
- **圖片提示詞草稿**：編輯、保存及復原角色圖與背景圖 Prompt。
            """
        )

    with st.expander("作品書架與目前作品"):
        st.markdown(
            """
- **作品書架**：建立、打開、重新命名或封存作品。
- **完整創作與精修**：把角色、世界與故事候選整理成可接受的正式內容。
- **專案角色／世界觀設定**：管理某一本作品內的角色與世界資料。
- **故事寫作**：處理需求、故事聖經、大綱、章節、場景與續寫。
- **正式提示詞工作台／圖片風格**：組合角色、場景、鏡頭與風格 Prompt。
            """
        )

    with st.expander("AI、模型與設定"):
        st.markdown(
            """
- **AI 設定**：選擇 OpenAI 或本機 Ollama，管理目前工作階段共用的模型設定。
- **圖片模型檔案庫**：唯讀盤點已登錄的本機圖片模型檔，不會下載或執行模型。
- **生成實驗室**：記錄匯回的外部生成結果、參數與人工評分，協助比較。
- **設定與備份**：檢查本機環境、資料庫與備份狀態。
            """
        )


def _data_and_backup() -> None:
    st.markdown(f"## {GUIDE_SECTION_TITLES[4]}")
    st.markdown(
        "你的作品預設保存在這台電腦。要搬到另一台電腦時，請使用「設定與備份」建立完整備份。"
    )
    with st.expander("在兩台電腦之間搬移作品"):
        st.markdown(
            """
1. 先在來源電腦建立備份，並關閉兩台電腦上的 Imaginarium Forge。
2. 把備份搬到目的電腦，再使用既有還原流程套用。
3. 不要讓兩台電腦同時開啟同一份資料庫檔案；備份是單次快照，不是同步服務。
4. 兩台電腦各自修改後不會自動合併；搬移前，先替即將被取代的一端再做備份。
            """
        )


def render() -> None:
    page_header(
        "簡易使用說明",
        "不必照固定順序創作。先做一張自由草稿，或直接打開一本書都可以。",
        eyebrow="從你現在最想做的事開始",
        badges=(("免專案也能創作", "teal"), ("AI 可選用", "")),
    )
    _quick_start()
    st.divider()
    _creative_paths()
    st.divider()
    _ai_and_privacy()
    st.divider()
    _feature_map()
    st.divider()
    _data_and_backup()
