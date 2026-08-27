"""A project-optional notebook for loose character ideas."""

from __future__ import annotations

import inspect
import json
import random
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, cast

import streamlit as st
from pydantic import SecretStr

from imaginarium_forge.application.errors import ApplicationError
from imaginarium_forge.application.services.character_biography_generation_service import (
    CharacterBiographyBrief,
    CharacterBiographyDraftSource,
    CharacterBiographyGenerationMode,
    CharacterBiographyGenerationService,
    GeneratedCharacterBiographyDraft,
)
from imaginarium_forge.application.services.creative_inspiration_service import (
    render_english_keywords,
)
from imaginarium_forge.domain.character.biography_draft import CharacterBiographyDraft
from imaginarium_forge.domain.character.gender import CharacterGender
from imaginarium_forge.ui.bootstrap import build_creative_provider, get_services
from imaginarium_forge.ui.character_draft_promotion import (
    prepare_character_draft_launchpad_handoff,
    validate_character_draft_promotion,
)
from imaginarium_forge.ui.components import page_header
from imaginarium_forge.ui.content_length import assess_text_length
from imaginarium_forge.ui.openai_session import (
    OPENAI_API_KEY_SESSION_KEY,
    apply_openai_session_state_transitions,
    consume_openai_key_after_action,
    render_openai_session_summary,
)
from imaginarium_forge.ui.provider_diagnostics import diagnose_provider_error

PAGE_KEY = "Character Drafts"
PAGE_LABEL = "角色與故事草稿"

STATE_PREFIX = "character_draft_box"
SELECTED_DRAFT_STATE_KEY = f"{STATE_PREFIX}_selected"
PENDING_SELECTION_STATE_KEY = f"{STATE_PREFIX}_pending_selection"
BOUND_DRAFT_STATE_KEY = f"{STATE_PREFIX}_bound"
PENDING_FORM_STATE_KEY = f"{STATE_PREFIX}_pending_form"
FLASH_STATE_KEY = f"{STATE_PREFIX}_flash"
ARCHIVE_CONFIRM_STATE_KEY = f"{STATE_PREFIX}_archive_confirm"
PROVIDER_GENERATION_HOOK_STATE_KEY = f"{STATE_PREFIX}_provider_generation_hook"
GENERATION_SOURCE_STATE_KEY: Final = f"{STATE_PREFIX}_generation_source"
GENERATION_CLUE_STATE_KEY: Final = f"{STATE_PREFIX}_generation_clue"
GENERATION_REVISION_STATE_KEY: Final = f"{STATE_PREFIX}_generation_revision"
GENERATION_ACTION_STATE_KEY: Final = f"{STATE_PREFIX}_generation_action"
GENERATION_ERROR_STATE_KEY: Final = f"{STATE_PREFIX}_generation_error"
GENERATION_LENGTH_RESULT_STATE_KEY: Final = f"{STATE_PREFIX}_generation_length_result"
GENERATION_PROVIDER_STATE_KEY: Final = f"{STATE_PREFIX}_injected_provider"
# Compatibility export for integrations that inject a masked session secret.
OPENAI_API_KEY_STATE_KEY: Final = OPENAI_API_KEY_SESSION_KEY
OPENAI_CONSENT_STATE_KEY: Final = f"{STATE_PREFIX}_openai_consent"
REMOTE_CONSENT_STATE_KEY: Final = f"{STATE_PREFIX}_remote_consent"
REMOTE_CONSENT_RESET_PENDING_STATE_KEY: Final = f"{STATE_PREFIX}_remote_consent_reset_pending"
REMOTE_CONSENT_RESET_SOURCE_STATE_KEY: Final = f"{STATE_PREFIX}_remote_consent_reset_source"

_NEW_DRAFT = "__new__"
_FORM_KEYS = {
    "title": f"{STATE_PREFIX}_title",
    "character_name": f"{STATE_PREFIX}_character_name",
    "gender": f"{STATE_PREFIX}_gender",
    "character_details": f"{STATE_PREFIX}_character_details",
    "character_image_prompt_en": f"{STATE_PREFIX}_character_image_prompt_en",
    "personal_story": f"{STATE_PREFIX}_personal_story",
    "background_image_prompt_en": f"{STATE_PREFIX}_background_image_prompt_en",
    "notes": f"{STATE_PREFIX}_notes",
    "link_project": f"{STATE_PREFIX}_link_project",
    "project_id": f"{STATE_PREFIX}_project_id",
}
_WRITE_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("draft_title",),
    "character_name": ("name",),
    "gender": ("character_gender",),
    "character_details": ("character_description",),
    "character_image_prompt_en": ("character_prompt",),
    "personal_story": ("background_story", "story"),
    "background_image_prompt_en": ("background_prompt",),
    "notes": ("memo",),
    "project_id": ("linked_project_id",),
}
_READ_ALIASES: dict[str, tuple[str, ...]] = {
    key: (key, *aliases) for key, aliases in _WRITE_ALIASES.items()
}
_READ_ALIASES["id"] = ("id", "draft_id")


@dataclass(frozen=True, slots=True)
class _DraftView:
    id: str
    title: str
    character_name: str
    gender: CharacterGender
    character_details: str
    character_image_prompt_en: str
    personal_story: str
    background_image_prompt_en: str
    notes: str
    project_id: str | None


@dataclass(frozen=True, slots=True)
class _GenerationScope:
    character_context: int
    story_context: int
    prompt_context: int
    notes_context: int
    character_target: int
    story_min_chars: int | None
    story_max_chars: int | None
    bounds_valid: bool


class _CharacterDraftAdapter:
    """Keep the page tolerant of the original provisional service vocabulary."""

    def __init__(self, service: Any) -> None:
        self._service = service

    @property
    def available(self) -> bool:
        return all(
            self._find_method(method) is not None
            for method in ("create", "update", "get", "list", "archive")
        )

    def list_active(self) -> list[Any]:
        method = self._required_method("list")
        kwargs: dict[str, Any] = {}
        if _accepts_parameter(method, "include_archived"):
            kwargs["include_archived"] = False
        records = method(**kwargs)
        return [record for record in records if not _is_archived(record)]

    def get(self, draft_id: str) -> Any:
        return self._required_method("get")(draft_id)

    def create(self, payload: Mapping[str, Any]) -> Any:
        method = self._required_method("create")
        return method(**_adapt_payload(method, payload))

    def update(self, draft_id: str, payload: Mapping[str, Any]) -> Any:
        method = self._required_method("update")
        return method(draft_id, **_adapt_payload(method, payload))

    def archive(self, draft_id: str) -> Any:
        return self._required_method("archive")(draft_id)

    def _required_method(self, name: str) -> Callable[..., Any]:
        method = self._find_method(name)
        if method is None:
            raise RuntimeError(f"角色草稿服務缺少 {name}()")
        return method

    def _find_method(self, name: str) -> Callable[..., Any] | None:
        official = {
            "create": "create_draft",
            "update": "update_draft",
            "get": "get_draft",
            "list": "list_drafts",
            "archive": "archive_draft",
        }[name]
        for candidate in (official, name):
            method = getattr(self._service, candidate, None)
            if callable(method):
                return cast(Callable[..., Any], method)
        return None


def _accepts_parameter(method: Callable[..., Any], name: str) -> bool:
    try:
        parameters = inspect.signature(method).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == name or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _adapt_payload(method: Callable[..., Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    """Map canonical fields once, before a mutating call can have side effects."""

    try:
        parameters = inspect.signature(method).parameters
    except (TypeError, ValueError):
        return dict(payload)
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return dict(payload)

    adapted: dict[str, Any] = {}
    for canonical, value in payload.items():
        candidates = (canonical, *_WRITE_ALIASES.get(canonical, ()))
        accepted = next((name for name in candidates if name in parameters), None)
        if accepted is not None:
            adapted[accepted] = value
    return adapted


def _record_value(record: Any, names: tuple[str, ...], default: Any = "") -> Any:
    if isinstance(record, Mapping):
        for name in names:
            if name in record:
                return record[name]
        return default
    for name in names:
        if hasattr(record, name):
            return getattr(record, name)
    return default


def _gender(value: Any) -> CharacterGender:
    if isinstance(value, CharacterGender):
        return value
    normalized = str(value or "").strip().casefold()
    if normalized in {"male", "男", "man", "adult man"}:
        return CharacterGender.MALE
    return CharacterGender.FEMALE


def _draft_view(record: Any) -> _DraftView:
    project_id = _record_value(record, _READ_ALIASES["project_id"], None)
    return _DraftView(
        id=str(_record_value(record, _READ_ALIASES["id"])),
        title=str(_record_value(record, _READ_ALIASES["title"])),
        character_name=str(_record_value(record, _READ_ALIASES["character_name"])),
        gender=_gender(_record_value(record, _READ_ALIASES["gender"])),
        character_details=str(_record_value(record, _READ_ALIASES["character_details"])),
        character_image_prompt_en=str(
            _record_value(record, _READ_ALIASES["character_image_prompt_en"])
        ),
        personal_story=str(_record_value(record, _READ_ALIASES["personal_story"])),
        background_image_prompt_en=str(
            _record_value(record, _READ_ALIASES["background_image_prompt_en"])
        ),
        notes=str(_record_value(record, _READ_ALIASES["notes"])),
        project_id=str(project_id) if project_id else None,
    )


def _is_archived(record: Any) -> bool:
    status = _record_value(record, ("status",), "")
    status_value = getattr(status, "value", status)
    if str(status_value).casefold() == "archived":
        return True
    return bool(_record_value(record, ("archived_at",), None))


def _blank_form() -> dict[str, Any]:
    return {
        "title": "",
        "character_name": "",
        "gender": CharacterGender.FEMALE,
        "character_details": "",
        "character_image_prompt_en": "",
        "personal_story": "",
        "background_image_prompt_en": "",
        "notes": "",
        "link_project": False,
        "project_id": None,
    }


def _form_from_draft(draft: _DraftView) -> dict[str, Any]:
    return {
        "title": draft.title,
        "character_name": draft.character_name,
        "gender": draft.gender,
        "character_details": draft.character_details,
        "character_image_prompt_en": draft.character_image_prompt_en,
        "personal_story": draft.personal_story,
        "background_image_prompt_en": draft.background_image_prompt_en,
        "notes": draft.notes,
        "link_project": draft.project_id is not None,
        "project_id": draft.project_id,
    }


def _write_form(values: Mapping[str, Any]) -> None:
    for field, key in _FORM_KEYS.items():
        st.session_state[key] = values.get(field, _blank_form()[field])


def _apply_pending_form() -> None:
    pending = st.session_state.pop(PENDING_FORM_STATE_KEY, None)
    if isinstance(pending, Mapping):
        for field, value in pending.items():
            key = _FORM_KEYS.get(str(field))
            if key is not None:
                st.session_state[key] = value


def _offline_inspiration(gender: CharacterGender, *, seed: int | None = None) -> dict[str, str]:
    """Build one coherent local character card without provider or network I/O."""

    picker: random.Random | random.SystemRandom
    picker = random.Random(seed) if seed is not None else random.SystemRandom()
    profiles = {
        CharacterGender.FEMALE: (
            (
                "璃央",
                "替陌生人保管未寄出信件的夜班郵差",
                "安靜敏銳，會把每個承諾記進袖口；害怕被需要之後又失去對方",
                ("adult woman", "long rose pink twin tails", "amber eyes", "gentle face"),
            ),
            (
                "祈夏",
                "能聽見舊建築夢話的修復師",
                "外柔內韌，總先照顧別人的裂痕；卻不肯承認自己也需要被接住",
                ("adult woman", "short silver bob hair", "blue eyes", "freckled face"),
            ),
            (
                "青棠",
                "在雨季替失蹤者畫回家地圖的製圖師",
                "觀察細膩，遇到危險反而冷靜；最大的盲點是把孤獨誤認成自由",
                ("adult woman", "dark braided hair", "gray eyes", "delicate features"),
            ),
        ),
        CharacterGender.MALE: (
            (
                "洛岑",
                "替陌生人保管未寄出信件的夜班郵差",
                "安靜敏銳，會把每個承諾記進袖口；害怕被需要之後又失去對方",
                ("adult man", "wavy black hair", "amber eyes", "gentle face"),
            ),
            (
                "聞澈",
                "能聽見舊建築夢話的修復師",
                "外冷內柔，總先修補別人的裂痕；卻不肯承認自己也需要被接住",
                ("adult man", "short silver hair", "blue eyes", "freckled face"),
            ),
            (
                "季遙",
                "在雨季替失蹤者畫回家地圖的製圖師",
                "觀察細膩，遇到危險反而冷靜；最大的盲點是把孤獨誤認成自由",
                ("adult man", "dark shoulder length hair", "gray eyes", "refined features"),
            ),
        ),
    }
    worlds = (
        (
            "只在退潮時出現的霧城",
            "多年以前，{name}沒能送達一封求救信。如今相同筆跡再次出現，收件人卻是明天的自己。"
            "為了找到寫信的人，{name}必須走進每次漲潮都會改寫記憶的舊城，也學會把真相交給一位值得信任的人。",
            ("misty tidal city", "blue hour", "wet stone streets", "distant lighthouse"),
            ("weathered travel coat", "canvas mailbag", "thoughtful expression"),
        ),
        (
            "永遠行駛在雲海上的末班列車",
            "{name}一直替乘客收藏不敢帶下車的回憶。某夜，一只盒子裡傳出自己童年的聲音，"
            "並預告列車會在天亮前失去最後一座車站。{name}必須決定留下守護所有人，或第一次追尋自己的歸處。",
            ("train above clouds", "warm carriage lights", "dawn sky", "vast cloud sea"),
            ("layered conductor coat", "memory box", "quiet determined expression"),
        ),
        (
            "由巨樹根系連起的地下聚落",
            "{name}能讀出樹皮上被抹去的承諾，卻發現其中一條出自失蹤多年的家人。"
            "追查使整座聚落的和平開始鬆動；若要讓真相重見天日，{name}必須先承認自己最想守住的其實是一段關係。",
            ("underground root city", "bioluminescent moss", "ancient trees", "soft green light"),
            ("practical layered outfit", "leather notebook", "alert gentle expression"),
        ),
    )
    name, role, personality, appearance = picker.choice(profiles[gender])
    world, story, background_prompt, wardrobe = picker.choice(worlds)
    # The role is Chinese and therefore belongs in prose only, never in the
    # explicitly English image prompt.
    character_prompt = (*appearance, *wardrobe)
    return {
        "title": f"{world}的{name}",
        "character_name": name,
        "character_details": (
            f"{name}是一名{role}。{personality}。外表與動作看似從容，碰到與失蹤者有關的線索時，"
            "會下意識反覆確認出口。真正想要的是一個能安心留下的地方；可延伸的人際線是一位知道舊案真相、"
            "卻始終沒有說破的搭檔。"
        ),
        "character_image_prompt_en": render_english_keywords(character_prompt),
        "personal_story": story.format(name=name),
        "background_image_prompt_en": render_english_keywords(background_prompt),
    }


def _form_payload(project_id: str | None) -> dict[str, Any]:
    return {
        "title": str(st.session_state.get(_FORM_KEYS["title"], "")).strip(),
        "character_name": str(st.session_state.get(_FORM_KEYS["character_name"], "")).strip(),
        "gender": _gender(st.session_state.get(_FORM_KEYS["gender"])),
        "character_details": str(st.session_state.get(_FORM_KEYS["character_details"], "")).strip(),
        "character_image_prompt_en": render_english_keywords(
            str(st.session_state.get(_FORM_KEYS["character_image_prompt_en"], ""))
        ),
        "personal_story": str(st.session_state.get(_FORM_KEYS["personal_story"], "")).strip(),
        "background_image_prompt_en": render_english_keywords(
            str(st.session_state.get(_FORM_KEYS["background_image_prompt_en"], ""))
        ),
        "notes": str(st.session_state.get(_FORM_KEYS["notes"], "")).strip(),
        "project_id": project_id,
    }


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", value.strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-")
    return (cleaned or "角色草稿")[:60]


def _json_export(payload: Mapping[str, Any], draft_id: str | None) -> str:
    gender = _gender(payload.get("gender"))
    body = {
        "schema_version": 1,
        "draft_id": draft_id,
        **payload,
        "gender": gender.value,
    }
    return json.dumps(body, ensure_ascii=False, indent=2)


def _txt_export(payload: Mapping[str, Any]) -> str:
    gender = _gender(payload.get("gender"))
    project_note = str(payload.get("project_id") or "未連結作品")
    return "\n\n".join(
        (
            f"角色草稿｜{payload.get('title', '')}",
            (
                f"角色名：{payload.get('character_name', '')}\n"
                f"性別：{gender.zh_label}\n作品連結：{project_note}"
            ),
            f"角色完整描述\n{payload.get('character_details', '')}",
            f"角色圖片 Prompt（English）\n{payload.get('character_image_prompt_en', '')}",
            f"個人故事\n{payload.get('personal_story', '')}",
            f"背景圖片 Prompt（English）\n{payload.get('background_image_prompt_en', '')}",
            f"備註\n{payload.get('notes', '')}",
        )
    )


def _generated_character_details(draft: GeneratedCharacterBiographyDraft) -> str:
    description = draft.character_description
    return "\n\n".join(
        (
            f"角色概覽\n{description.name} · {description.age_suggestion} 歲 · "
            f"{description.identity}",
            f"完整背景\n{description.biography}",
            f"個性\n{description.personality}",
            f"說話與聲音\n{description.voice}",
            f"真正想要的事\n{description.motivation}",
            f"恐懼\n{description.fear}",
            f"藏著的祕密\n{description.secret}",
            f"內在拉扯\n{description.internal_conflict}",
            "可延伸的人際線\n- " + "\n- ".join(description.relationship_hooks),
            (
                f"角色弧線\n起點：{description.arc_start}\n"
                f"轉折：{'；'.join(description.arc_turning_points)}\n"
                f"終點：{description.arc_end}"
            ),
            (
                f"外觀記事\n臉部：{description.face}\n髮型：{description.hair}\n"
                f"眼睛：{description.eyes}\n體態：{description.body}\n"
                f"識別特徵：{'；'.join(description.distinguishing_features)}"
            ),
            "不要自動改動\n- " + "\n- ".join(description.prohibited_mutations),
            f"常見動作與表情\n{description.action}；{description.expression}",
        )
    )


def _generated_form(draft: GeneratedCharacterBiographyDraft) -> dict[str, Any]:
    description = draft.character_description
    return {
        "title": f"{description.name}的小傳",
        "character_name": description.name,
        "gender": description.gender,
        "character_details": _generated_character_details(draft),
        "character_image_prompt_en": draft.character_image_prompt_en,
        "personal_story": draft.personal_story,
        "background_image_prompt_en": draft.background_image_prompt_en,
    }


def _merge_generated_form(
    generated: Mapping[str, Any], *, fill_blanks_only: bool
) -> dict[str, Any]:
    pending: dict[str, Any] = {}
    for field, value in generated.items():
        current = st.session_state.get(_FORM_KEYS[field], "")
        if field == "gender":
            pending[field] = current
        elif field in {"title", "character_name"}:
            pending[field] = str(current).strip() or value
        elif fill_blanks_only:
            pending[field] = current if str(current).strip() else value
        else:
            pending[field] = value
    return pending


def _bounded_author_context(
    *,
    clue: str,
    character_limit: int,
    story_limit: int,
    prompt_limit: int,
    notes_limit: int,
) -> str:
    """Build a bounded provider context while keeping every allocation author-controlled."""

    sections: list[str] = []
    if clue.strip():
        sections.append(f"一句線索：{clue.strip()[:1200]}")
    sources = (
        (
            "目前角色設定",
            str(st.session_state.get(_FORM_KEYS["character_details"], "")),
            character_limit,
        ),
        (
            "目前個人故事",
            str(st.session_state.get(_FORM_KEYS["personal_story"], "")),
            story_limit,
        ),
        (
            "目前角色圖片提示詞",
            str(st.session_state.get(_FORM_KEYS["character_image_prompt_en"], "")),
            prompt_limit,
        ),
        (
            "目前背景圖片提示詞",
            str(st.session_state.get(_FORM_KEYS["background_image_prompt_en"], "")),
            prompt_limit,
        ),
        ("頁邊備註", str(st.session_state.get(_FORM_KEYS["notes"], "")), notes_limit),
    )
    for label, value, limit in sources:
        if limit > 0 and value.strip():
            sections.append(f"{label}：{value.strip()[:limit]}")
    combined = "\n\n".join(sections)
    return (combined or "請自由生成一名完整且前後一致的角色。")[:4000]


def _generation_revision(
    author_revision: str,
    *,
    character_target: int,
    story_min_chars: int | None,
    story_max_chars: int | None,
) -> str:
    story_request = ""
    if story_min_chars is not None and story_max_chars is not None:
        story_request = (
            f"個人故事請寫在 {story_min_chars} 至 {story_max_chars} 字之間，"
            "以不含空白的可見字元計算。"
        )
    elif story_min_chars is not None:
        story_request = (
            f"個人故事至少 {story_min_chars} 字，以不含空白的可見字元計算；"
            "請用有效情節與人物細節補足，不要重複灌水。"
        )
    elif story_max_chars is not None:
        story_request = (
            f"個人故事最多 {story_max_chars} 字，以不含空白的可見字元計算；"
            "請自然收尾，不要突然截斷。"
        )
    length_request = f"角色完整設定以約 {character_target} 個中文字為目標。{story_request}"
    return "\n".join(part for part in (author_revision.strip(), length_request) if part)[:4000]


def _render_generation_scope_controls() -> _GenerationScope:
    st.caption("你可以分別決定舊文字要帶入多少，以及新內容希望寫多長；0 代表不帶入該欄。")
    context_columns = st.columns(4)
    character_limit = int(
        context_columns[0].number_input(
            "角色設定帶入字數",
            min_value=0,
            max_value=3000,
            value=1600,
            step=100,
            key=f"{STATE_PREFIX}_character_context_limit",
        )
    )
    story_limit = int(
        context_columns[1].number_input(
            "個人故事帶入字數",
            min_value=0,
            max_value=3000,
            value=1600,
            step=100,
            key=f"{STATE_PREFIX}_story_context_limit",
        )
    )
    prompt_limit = int(
        context_columns[2].number_input(
            "每組提示詞帶入字數",
            min_value=0,
            max_value=1200,
            value=600,
            step=100,
            key=f"{STATE_PREFIX}_prompt_context_limit",
        )
    )
    notes_limit = int(
        context_columns[3].number_input(
            "備註帶入字數",
            min_value=0,
            max_value=1200,
            value=600,
            step=100,
            key=f"{STATE_PREFIX}_notes_context_limit",
        )
    )
    target_columns = st.columns(2)
    character_target = int(
        target_columns[0].number_input(
            "新角色設定目標字數",
            min_value=200,
            max_value=3000,
            value=900,
            step=100,
            key=f"{STATE_PREFIX}_character_target_length",
        )
    )
    use_story_bounds = target_columns[1].checkbox(
        "限制新個人故事字數",
        value=False,
        key=f"{STATE_PREFIX}_use_story_length_bounds",
        help="可指定至少、最多，或一段上下限；不勾選時讓生成器自由決定長度。",
    )
    story_length_mode = st.radio(
        "個人故事怎麼限制？",
        options=("range", "minimum", "maximum"),
        format_func=lambda value: {
            "range": "設定範圍",
            "minimum": "至少這麼長",
            "maximum": "最多這麼長",
        }[value],
        horizontal=True,
        key=f"{STATE_PREFIX}_story_length_mode",
        disabled=not use_story_bounds,
    )
    story_columns = st.columns(2)
    story_min_value = int(
        story_columns[0].number_input(
            "個人故事最少字數",
            min_value=200,
            max_value=12_000,
            value=1_000,
            step=100,
            key=f"{STATE_PREFIX}_story_target_length",
            disabled=not use_story_bounds or story_length_mode == "maximum",
        )
    )
    story_max_value = int(
        story_columns[1].number_input(
            "個人故事最多字數",
            min_value=200,
            max_value=12_000,
            value=1_800,
            step=100,
            key=f"{STATE_PREFIX}_story_max_length",
            disabled=not use_story_bounds or story_length_mode == "minimum",
        )
    )
    story_min_chars = (
        story_min_value if use_story_bounds and story_length_mode in {"range", "minimum"} else None
    )
    story_max_chars = (
        story_max_value if use_story_bounds and story_length_mode in {"range", "maximum"} else None
    )
    bounds_valid = not (
        story_min_chars is not None
        and story_max_chars is not None
        and story_min_chars > story_max_chars
    )
    if use_story_bounds:
        st.caption(
            "字數以不含空白的可見字元計算；本機／OpenAI 會收到這項要求，"
            "離線素材則會顯示實際差距。模型或帳戶本身的單次輸出上限仍可能讓成品較短。"
        )
    if not bounds_valid:
        st.error("個人故事的最少字數不可大於最多字數。")
    return _GenerationScope(
        character_context=character_limit,
        story_context=story_limit,
        prompt_context=prompt_limit,
        notes_context=notes_limit,
        character_target=character_target,
        story_min_chars=story_min_chars,
        story_max_chars=story_max_chars,
        bounds_valid=bounds_valid,
    )


def _render_provider_picker(
    services: Any,
) -> tuple[str, str, bool, SecretStr | None]:
    source = st.radio(
        "怎麼補完？",
        options=("offline", "local", "openai"),
        format_func=lambda value: {
            "offline": "完全離線",
            "local": "本機 Ollama",
            "openai": "OpenAI",
        }[value],
        horizontal=True,
        key=GENERATION_SOURCE_STATE_KEY,
    )
    if source == "offline":
        st.caption("使用內建角色素材，不需要 AI、API Key 或網路。")
        return source, "", True, None

    injected_provider = st.session_state.get(GENERATION_PROVIDER_STATE_KEY)
    if source == "openai":
        shared = render_openai_session_summary(key_prefix=STATE_PREFIX)
        try:
            runtime = shared.runtime_config()
            model = runtime.model
        except ValueError:
            runtime = None
            model = ""
            st.error("AI 設定中的自訂模型 ID 尚未填完整。")
        has_key = shared.configured or injected_provider is not None
        if not has_key:
            st.info("請先到 AI 設定放入 API Key，或設定系統環境變數 OPENAI_API_KEY。")
        st.warning("只有按下生成後，這次的線索與目前填寫的草稿內容才會送到 OpenAI。")
        consent = st.checkbox(
            "只同意這一次傳送上述創作內容到 OpenAI",
            key=OPENAI_CONSENT_STATE_KEY,
            disabled=not has_key,
        )
        return (
            source,
            model,
            bool(model) and has_key and consent,
            runtime.api_key_for_provider() if runtime is not None else None,
        )

    from imaginarium_forge.ui.pages.creative_launchpad import _endpoint_summary

    endpoint = _endpoint_summary(services.settings.ollama_base_url)
    model = st.text_input(
        "Ollama 模型名稱",
        value=services.settings.default_model,
        placeholder="例如：qwen3:8b",
        key=f"{STATE_PREFIX}_local_model",
    ).strip()
    if not endpoint.valid:
        st.error(f"Ollama 端點設定無效：{endpoint.redacted_origin}")
        return source, model, False, None
    if endpoint.is_loopback:
        st.caption(f"本機模型端點：{endpoint.redacted_origin}")
        return source, model, bool(model), None
    st.warning(f"這個 Ollama 端點不是本機：{endpoint.redacted_origin}。創作內容會離開電腦。")
    if not endpoint.uses_tls:
        st.error("遠端端點沒有使用 HTTPS，因此不允許傳送內容。")
        return source, model, False, None
    consent = st.checkbox(
        "只同意這一次傳送內容到遠端 Ollama",
        key=REMOTE_CONSENT_STATE_KEY,
    )
    return source, model, bool(model) and consent, None


def _render_provider_generation_hook(services: Any) -> None:
    """Generate one portable biography without touching persistence or Project state."""

    length_result = st.session_state.get(GENERATION_LENGTH_RESULT_STATE_KEY)
    if isinstance(length_result, dict):
        length_message = str(length_result.get("message", ""))
        if length_result.get("state") == "within":
            st.success(length_message)
        elif length_result.get("state") in {"below", "above"}:
            st.warning(length_message)
        elif length_message:
            st.caption(length_message)

    with st.expander("想讓系統把零星想法補成完整角色？"):
        clue = st.text_area(
            "一句線索（選填）",
            placeholder="例如：可愛的女孩子，孤單，雙馬尾，粉紅色頭髮",
            height=90,
            key=GENERATION_CLUE_STATE_KEY,
        )
        revision = st.text_area(
            "這一版想怎麼調整？（選填）",
            placeholder="例如：保留粉紅雙馬尾，把職業改成夜班列車員，故事更溫柔。",
            height=90,
            key=GENERATION_REVISION_STATE_KEY,
        )
        with st.expander("生成內容的長短與參考範圍"):
            generation_scope = _render_generation_scope_controls()
        source, model, provider_ready, transient_api_key = _render_provider_picker(services)
        action = st.radio(
            "新內容要怎麼落到紙上？",
            options=("fill", "rewrite"),
            format_func=lambda value: (
                "只補空白，保留我已寫的欄位" if value == "fill" else "依照指示重寫內容"
            ),
            horizontal=True,
            key=GENERATION_ACTION_STATE_KEY,
        )
        st.caption("角色名若已填寫會被鎖定；角色性別永遠以你上方的選擇為準。")
        if st.button(
            "✦ 補成一張完整角色紙頁",
            key=f"{STATE_PREFIX}_generate_complete",
            type="primary",
            use_container_width=True,
            disabled=not provider_ready or not generation_scope.bounds_valid,
        ):
            st.session_state.pop(GENERATION_LENGTH_RESULT_STATE_KEY, None)
            gender = _gender(st.session_state.get(_FORM_KEYS["gender"]))
            preferred_name = str(st.session_state.get(_FORM_KEYS["character_name"], "")).strip()
            injected_provider = st.session_state.get(GENERATION_PROVIDER_STATE_KEY)
            provider = injected_provider
            owns_provider = False
            mode = CharacterBiographyGenerationMode.NO_LLM
            if source == "local":
                mode = CharacterBiographyGenerationMode.OLLAMA
            elif source == "openai":
                mode = CharacterBiographyGenerationMode.OPENAI
            if mode is not CharacterBiographyGenerationMode.NO_LLM:
                st.session_state[REMOTE_CONSENT_RESET_PENDING_STATE_KEY] = True
                st.session_state[REMOTE_CONSENT_RESET_SOURCE_STATE_KEY] = source
            generation: CharacterBiographyGenerationService | None = None
            try:
                brief = CharacterBiographyBrief(
                    partial_clues=_bounded_author_context(
                        clue=clue,
                        character_limit=generation_scope.character_context,
                        story_limit=generation_scope.story_context,
                        prompt_limit=generation_scope.prompt_context,
                        notes_limit=generation_scope.notes_context,
                    ),
                    selected_gender=gender,
                    preferred_name=preferred_name,
                    revision_instruction=_generation_revision(
                        revision,
                        character_target=generation_scope.character_target,
                        story_min_chars=generation_scope.story_min_chars,
                        story_max_chars=generation_scope.story_max_chars,
                    ),
                    personal_story_min_chars=generation_scope.story_min_chars,
                    personal_story_max_chars=generation_scope.story_max_chars,
                )
                if mode is not CharacterBiographyGenerationMode.NO_LLM and provider is None:
                    provider = build_creative_provider(
                        services.settings,
                        provider_name="openai" if source == "openai" else "ollama",
                        api_key=transient_api_key,
                    )
                    owns_provider = True
                generation = CharacterBiographyGenerationService(
                    provider,
                    owns_provider=owns_provider,
                )
                with st.spinner("正在把角色、個人故事與兩組提示詞整理成同一個人……"):
                    result = generation.generate(brief, mode=mode, model=model)
            except Exception as exc:
                diagnostic = diagnose_provider_error(
                    exc,
                    provider_label="OpenAI" if source == "openai" else "本機 Ollama",
                )
                st.session_state[GENERATION_ERROR_STATE_KEY] = (
                    f"{diagnostic.message_zh_tw} 紙上的內容完全沒變。"
                )
            else:
                if (
                    mode is not CharacterBiographyGenerationMode.NO_LLM
                    and result.provenance.source is not CharacterBiographyDraftSource.PROVIDER
                ):
                    provider_label = "OpenAI" if source == "openai" else "Ollama"
                    st.session_state[GENERATION_ERROR_STATE_KEY] = (
                        f"{provider_label} 這次沒有完成，紙上的內容完全沒變；"
                        "可檢查模型、權限或連線後重試。"
                    )
                else:
                    assessment = assess_text_length(
                        result.draft.personal_story,
                        minimum=generation_scope.story_min_chars,
                        maximum=generation_scope.story_max_chars,
                    )
                    st.session_state[GENERATION_LENGTH_RESULT_STATE_KEY] = {
                        "actual": assessment.actual,
                        "minimum": assessment.minimum,
                        "maximum": assessment.maximum,
                        "state": assessment.state,
                        "message": assessment.message,
                    }
                    st.session_state[PENDING_FORM_STATE_KEY] = _merge_generated_form(
                        _generated_form(result.draft),
                        fill_blanks_only=action == "fill",
                    )
                    provider_label = {
                        "offline": "離線素材",
                        "local": "本機 Ollama",
                        "openai": f"OpenAI · {result.provenance.model_used or model}",
                    }[source]
                    st.session_state[FLASH_STATE_KEY] = (
                        f"{provider_label} 已補成完整紙頁；尚未保存，每一行仍可自由改寫。"
                    )
            finally:
                if generation is not None:
                    generation.close()
                if source == "openai":
                    consume_openai_key_after_action(st.session_state)
            st.session_state[PROVIDER_GENERATION_HOOK_STATE_KEY] = {
                "source": source,
                "model": model,
            }
            transient_api_key = None
            st.rerun()

    st.session_state.setdefault(PROVIDER_GENERATION_HOOK_STATE_KEY, {})


def _list_projects(services: Any) -> list[Any]:
    service = getattr(services, "projects", None)
    method = getattr(service, "list_projects", None)
    if not callable(method):
        return []
    try:
        return list(method(include_archived=True))
    except (ApplicationError, TypeError):
        return []


def _project_label(project: Any) -> str:
    name = str(_record_value(project, ("name",), "未命名作品"))
    status = _record_value(project, ("status",), "")
    status_value = str(getattr(status, "value", status)).casefold()
    return f"{name}（已收起）" if status_value == "archived" else name


def _render_project_link(services: Any) -> str | None:
    projects = _list_projects(services)
    by_id = {str(_record_value(project, ("id",))): project for project in projects}
    with st.expander("要替這張草稿夾上一張作品書籤嗎？（選填）"):
        link = st.checkbox(
            "記住它可能屬於哪一本作品",
            key=_FORM_KEYS["link_project"],
            disabled=not projects,
        )
        if not projects:
            st.caption("現在沒有可連結的作品；草稿仍可獨立保存，完全不受影響。")
            return None
        if not link:
            st.caption("先放在草稿箱也很好。這不會自動加入作品角色或改動作品。")
            return None

        options = list(by_id)
        current = st.session_state.get(_FORM_KEYS["project_id"])
        if current not in options:
            selected_project = str(st.session_state.get("selected_project_id") or "")
            st.session_state[_FORM_KEYS["project_id"]] = (
                selected_project if selected_project in options else options[0]
            )
        project_id = st.selectbox(
            "哪一本作品？",
            options,
            format_func=lambda value: _project_label(by_id[value]),
            key=_FORM_KEYS["project_id"],
        )
        st.caption("這只是替草稿做連結；等你準備好時，再把它升級成正式角色。")
        return str(project_id)


def _draft_matches_payload(
    draft: CharacterBiographyDraft,
    payload: Mapping[str, Any],
) -> bool:
    comparable_fields = (
        "title",
        "character_name",
        "gender",
        "character_details",
        "character_image_prompt_en",
        "personal_story",
        "background_image_prompt_en",
        "notes",
        "project_id",
    )
    for field in comparable_fields:
        current = payload.get(field)
        saved = getattr(draft, field)
        if field == "gender":
            if _gender(current) is not saved:
                return False
        elif current != saved:
            return False
    return True


def _as_character_draft(record: Any) -> CharacterBiographyDraft:
    if isinstance(record, CharacterBiographyDraft):
        return record
    if isinstance(record, Mapping):
        return CharacterBiographyDraft.model_validate(record)
    model_dump = getattr(record, "model_dump", None)
    if callable(model_dump):
        return CharacterBiographyDraft.model_validate(model_dump(mode="python"))
    raise TypeError("目前的草稿服務無法提供搬上書架所需的完整資料")


def _render_promotion(
    *,
    services: Any,
    adapter: _CharacterDraftAdapter,
    selected: str,
    payload: Mapping[str, Any],
    prompt_error: str,
) -> None:
    st.markdown("#### 想把這張紙頁搬上書架嗎？")
    st.caption("完全選填。普通保存只會留在草稿箱；只有下面的確認按鈕會建立或連結作品。")
    if selected == _NEW_DRAFT:
        st.info("先把這張紙頁收進草稿箱；保存後才會出現搬上書架的選項。")
        return

    try:
        saved_draft = _as_character_draft(adapter.get(selected))
    except (ApplicationError, RuntimeError, TypeError, ValueError) as exc:
        st.error(f"目前無法準備搬上書架：{exc}")
        return
    dirty = bool(prompt_error) or not _draft_matches_payload(saved_draft, payload)
    if dirty:
        st.warning("紙頁上有尚未保存的修改；請先按「保存這張紙頁」，再搬上書架。")

    projects = [
        project
        for project in _list_projects(services)
        if str(getattr(_record_value(project, ("status",), "active"), "value", "active"))
        == "active"
    ]
    by_id = {str(_record_value(project, ("id",))): project for project in projects}
    with st.expander("搬上書架的去向"):
        options = ("new", "existing") if projects else ("new",)
        destination = st.radio(
            "要放到哪裡？",
            options=options,
            format_func=lambda value: (
                "以這名角色建立一本新作品" if value == "new" else "放進已有作品"
            ),
            horizontal=True,
            key=f"{STATE_PREFIX}_promotion_destination",
        )
        project_id: str | None = None
        project_name = ""
        if destination == "new":
            project_name = st.text_input(
                "新作品名稱",
                value=saved_draft.title,
                key=f"{STATE_PREFIX}_promotion_new_name_{selected}",
            ).strip()
            st.caption("會先建立一個只有書名的作品，再把角色草稿攤到精修工作台。")
        else:
            project_options = list(by_id)
            selection_key = f"{STATE_PREFIX}_promotion_existing_project"
            preferred = saved_draft.project_id or str(
                st.session_state.get("selected_project_id") or ""
            )
            if st.session_state.get(selection_key) not in project_options:
                st.session_state[selection_key] = (
                    preferred if preferred in project_options else project_options[0]
                )
            project_id = str(
                st.selectbox(
                    "選一本作品",
                    project_options,
                    format_func=lambda value: _project_label(by_id[value]),
                    key=selection_key,
                )
            )
            st.caption("這只會先交給精修工作台，不會直接建立正式角色內容、章節或劇本。")

        if st.button(
            "確定搬上書架，打開精修工作台",
            key=f"{STATE_PREFIX}_promote",
            type="primary",
            use_container_width=True,
            disabled=dirty or (destination == "new" and not project_name),
        ):
            created_project = False
            try:
                # This is a side-effect-free full-contract check and therefore
                # must run before an explicitly requested new Project is made.
                validate_character_draft_promotion(saved_draft)
                if destination == "new":
                    project = services.projects.create_project(
                        name=project_name,
                        description=(
                            f"從角色草稿〈{saved_draft.title}〉開始；"
                            "是否擴充成完整作品，留待精修工作台決定。"
                        ),
                    )
                    project_id = str(project.id)
                    created_project = True
                if not project_id:
                    raise ValueError("尚未選擇作品")
                handoff = prepare_character_draft_launchpad_handoff(
                    saved_draft,
                    project_id=project_id,
                )
                adapter.update(selected, {"project_id": project_id})
                for key in tuple(st.session_state):
                    if any(
                        str(key).startswith(prefix) for prefix in handoff.session_prefixes_to_clear
                    ):
                        st.session_state.pop(key, None)
                st.session_state.update(handoff.session_updates)
                st.rerun()
            except (ApplicationError, RuntimeError, TypeError, ValueError) as exc:
                suffix = "；新作品可能已建立，請到書架確認" if created_project else ""
                st.error(f"這次沒有搬上書架：{exc}{suffix}")


def render(*, services: Any | None = None) -> None:
    """Render the draft box; a project is deliberately not a prerequisite."""

    apply_openai_session_state_transitions(st.session_state)
    if st.session_state.pop(REMOTE_CONSENT_RESET_PENDING_STATE_KEY, False):
        consent_source = st.session_state.pop(
            REMOTE_CONSENT_RESET_SOURCE_STATE_KEY,
            "",
        )
        if consent_source == "openai":
            st.session_state.pop(OPENAI_CONSENT_STATE_KEY, None)
        else:
            st.session_state.pop(REMOTE_CONSENT_STATE_KEY, None)

    services = services or get_services()
    _apply_pending_form()
    pending_selection = st.session_state.pop(PENDING_SELECTION_STATE_KEY, None)
    if pending_selection is not None:
        st.session_state[SELECTED_DRAFT_STATE_KEY] = str(pending_selection)

    raw_service = getattr(services, "character_biography_drafts", None)
    if raw_service is None:
        # Compatibility for the provisional name used before the persistence
        # contract was finalized.
        raw_service = getattr(services, "character_drafts", None)
    adapter = _CharacterDraftAdapter(raw_service)
    persistence_ready = raw_service is not None and adapter.available

    page_header(
        PAGE_LABEL,
        "先收下一個角色念頭，不必先建立作品。名字、故事與圖片提示都能慢慢補齊。",
        eyebrow="一疊可以隨手翻寫的角色紙頁",
        badges=(("無專案也能開始", "teal"), ("離線靈感可用", "amber")),
    )
    # Keep the high-value offline action visually beside the page status while
    # rendering its widgets only after the selected draft has been bound below.
    offline_action_slot = st.container(border=True)
    flash = st.session_state.pop(FLASH_STATE_KEY, None)
    if isinstance(flash, str) and flash:
        st.success(flash)
    generation_error = st.session_state.pop(GENERATION_ERROR_STATE_KEY, None)
    if isinstance(generation_error, str) and generation_error:
        st.error(generation_error)

    records: list[Any] = []
    if persistence_ready:
        try:
            records = adapter.list_active()
        except (ApplicationError, RuntimeError, TypeError, ValueError) as exc:
            st.error(f"草稿箱暫時翻不開：{exc}")
    else:
        st.info(
            "草稿箱的本機保存服務正在接上書架。你仍可離線隨機填滿、編輯與匯出；"
            "保存按鈕會在服務就緒後啟用。"
        )

    views = [_draft_view(record) for record in records]
    by_id = {draft.id: draft for draft in views if draft.id}
    options = [_NEW_DRAFT, *by_id]
    selected_before = st.session_state.get(SELECTED_DRAFT_STATE_KEY)
    if selected_before not in options:
        st.session_state[SELECTED_DRAFT_STATE_KEY] = _NEW_DRAFT

    selector, new_action = st.columns((4, 1))
    with selector:
        selected = st.selectbox(
            "翻開哪張草稿？",
            options,
            format_func=lambda value: (
                "＋ 一張新的角色草稿"
                if value == _NEW_DRAFT
                else f"{by_id[value].title or '未命名草稿'} · "
                f"{by_id[value].character_name or '角色待命名'} · {value[-6:]}"
            ),
            key=SELECTED_DRAFT_STATE_KEY,
        )
    with new_action:
        st.write("")
        if st.button(
            "新紙頁",
            key=f"{STATE_PREFIX}_new",
            use_container_width=True,
            disabled=selected == _NEW_DRAFT,
        ):
            st.session_state[PENDING_SELECTION_STATE_KEY] = _NEW_DRAFT
            st.rerun()

    bound = st.session_state.get(BOUND_DRAFT_STATE_KEY)
    if selected != bound:
        st.session_state.pop(GENERATION_LENGTH_RESULT_STATE_KEY, None)
        st.session_state[ARCHIVE_CONFIRM_STATE_KEY] = None
        if selected == _NEW_DRAFT:
            _write_form(_blank_form())
        else:
            try:
                _write_form(_form_from_draft(_draft_view(adapter.get(selected))))
            except (ApplicationError, RuntimeError, TypeError, ValueError) as exc:
                st.error(f"這張草稿暫時讀不到：{exc}")
        st.session_state[BOUND_DRAFT_STATE_KEY] = selected

    st.caption("切換紙頁前，記得先按「收進草稿箱」。")
    identity = st.columns((3, 3, 2))
    identity[0].text_input(
        "這張草稿叫什麼？",
        placeholder="例如：雨夜郵差／粉紅雙馬尾女孩",
        key=_FORM_KEYS["title"],
    )
    identity[1].text_input(
        "角色名",
        placeholder="可以自己命名，也可留空後抽一個",
        key=_FORM_KEYS["character_name"],
    )
    identity[2].radio(
        "角色性別",
        tuple(CharacterGender),
        format_func=lambda value: value.zh_label,
        horizontal=True,
        key=_FORM_KEYS["gender"],
    )

    st.text_area(
        "這個人完整是什麼樣子？",
        placeholder="外貌、身分、個性、渴望、弱點、習慣與人際鉤子，都可以寫在這裡。",
        height=180,
        key=_FORM_KEYS["character_details"],
    )
    st.text_area(
        "角色圖片提示詞（英文，逗號分隔）",
        placeholder="pink twin tails, amber eyes, gentle expression, ...",
        height=110,
        key=_FORM_KEYS["character_image_prompt_en"],
    )
    st.text_area(
        "她／他的個人故事",
        placeholder="可以是一段過去、一個秘密，也可以是整條角色弧線。",
        height=180,
        key=_FORM_KEYS["personal_story"],
    )
    st.text_area(
        "背景圖片提示詞（英文，逗號分隔）",
        placeholder="misty tidal city, blue hour, wet stone street, ...",
        height=110,
        key=_FORM_KEYS["background_image_prompt_en"],
    )
    st.text_area(
        "頁邊備註",
        placeholder="還沒決定的事、禁用元素、之後想補的細節……",
        height=100,
        key=_FORM_KEYS["notes"],
    )

    # Populate the earlier visual slot only after every editor widget has been
    # mounted.  The button's explicit rerun therefore cannot make Streamlit
    # discard authored values that are outside the generated field set.
    with offline_action_slot:
        random_controls = st.columns((2, 3), vertical_alignment="center")
        preserve_written = random_controls[1].checkbox(
            "保留已經寫好的內容，只補空白",
            value=True,
            key=f"{STATE_PREFIX}_random_preserve",
        )
        if random_controls[0].button(
            "✦ 離線隨機填滿",
            key=f"{STATE_PREFIX}_randomize",
            type="primary",
            use_container_width=True,
        ):
            current_gender = _gender(st.session_state.get(_FORM_KEYS["gender"]))
            generated = _offline_inspiration(current_gender)
            pending_form: dict[str, Any] = {}
            for field, value in generated.items():
                current = str(st.session_state.get(_FORM_KEYS[field], "")).strip()
                if preserve_written:
                    pending_form[field] = current or value
                else:
                    pending_form[field] = value
            st.session_state[PENDING_FORM_STATE_KEY] = pending_form
            st.session_state[FLASH_STATE_KEY] = "離線靈感已落在紙上；每一行都還能自由改寫。"
            st.rerun()

    _render_provider_generation_hook(services)
    project_id = _render_project_link(services)

    try:
        payload = _form_payload(project_id)
        prompt_error = ""
    except ValueError as exc:
        payload = {
            "title": str(st.session_state.get(_FORM_KEYS["title"], "")).strip(),
            "character_name": str(st.session_state.get(_FORM_KEYS["character_name"], "")).strip(),
            "gender": _gender(st.session_state.get(_FORM_KEYS["gender"])),
            "character_details": str(
                st.session_state.get(_FORM_KEYS["character_details"], "")
            ).strip(),
            "character_image_prompt_en": str(
                st.session_state.get(_FORM_KEYS["character_image_prompt_en"], "")
            ).strip(),
            "personal_story": str(st.session_state.get(_FORM_KEYS["personal_story"], "")).strip(),
            "background_image_prompt_en": str(
                st.session_state.get(_FORM_KEYS["background_image_prompt_en"], "")
            ).strip(),
            "notes": str(st.session_state.get(_FORM_KEYS["notes"], "")).strip(),
            "project_id": project_id,
        }
        prompt_error = str(exc)
        st.warning(f"圖片提示詞需要維持英文、並用逗號分開：{exc}")

    save_column, archive_column = st.columns((3, 2))
    if save_column.button(
        "收進草稿箱" if selected == _NEW_DRAFT else "保存這張紙頁",
        key=f"{STATE_PREFIX}_save",
        type="primary",
        use_container_width=True,
        disabled=not persistence_ready or bool(prompt_error),
    ):
        if not str(payload["title"]).strip():
            st.error("先替這張草稿取一個名字，之後才找得回來。")
        elif not str(payload["character_name"]).strip():
            st.error("先替角色留一個名字；之後仍可隨時改名。")
        else:
            try:
                saved = (
                    adapter.create(payload)
                    if selected == _NEW_DRAFT
                    else adapter.update(selected, payload)
                )
                saved_id = _draft_view(saved).id
                st.session_state[PENDING_SELECTION_STATE_KEY] = saved_id or selected
                st.session_state[BOUND_DRAFT_STATE_KEY] = None
                st.session_state[FLASH_STATE_KEY] = (
                    "角色草稿已收好。" if selected == _NEW_DRAFT else "這張角色紙頁已保存。"
                )
                st.rerun()
            except (ApplicationError, RuntimeError, TypeError, ValueError) as exc:
                st.error(f"這次沒有保存成功：{exc}")

    if selected != _NEW_DRAFT:
        confirm_id = st.session_state.get(ARCHIVE_CONFIRM_STATE_KEY)
        if confirm_id != selected:
            if archive_column.button(
                "把這張草稿收起來",
                key=f"{STATE_PREFIX}_archive_start",
                use_container_width=True,
            ):
                st.session_state[ARCHIVE_CONFIRM_STATE_KEY] = selected
                st.rerun()
        else:
            archive_column.warning("收起後不會刪除內容，但會離開目前草稿清單。")
            confirm, cancel = archive_column.columns(2)
            if confirm.button(
                "確定收起",
                key=f"{STATE_PREFIX}_archive_confirm_action",
                type="primary",
                use_container_width=True,
            ):
                try:
                    adapter.archive(selected)
                    st.session_state[PENDING_SELECTION_STATE_KEY] = _NEW_DRAFT
                    st.session_state[BOUND_DRAFT_STATE_KEY] = None
                    st.session_state[ARCHIVE_CONFIRM_STATE_KEY] = None
                    st.session_state[FLASH_STATE_KEY] = "草稿已收起，內容沒有被刪除。"
                    st.rerun()
                except (ApplicationError, RuntimeError, TypeError, ValueError) as exc:
                    st.error(f"這次沒有收起成功：{exc}")
            if cancel.button(
                "先不要",
                key=f"{STATE_PREFIX}_archive_cancel",
                use_container_width=True,
            ):
                st.session_state[ARCHIVE_CONFIRM_STATE_KEY] = None
                st.rerun()

    _render_promotion(
        services=services,
        adapter=adapter,
        selected=selected,
        payload=payload,
        prompt_error=prompt_error,
    )

    st.markdown("#### 帶走這張紙頁")
    export_name = _safe_filename(
        str(payload.get("title") or payload.get("character_name") or "角色草稿")
    )
    exports = st.columns(2)
    exports[0].download_button(
        "下載 TXT",
        data=_txt_export(payload),
        file_name=f"{export_name}.txt",
        mime="text/plain; charset=utf-8",
        key=f"{STATE_PREFIX}_export_txt",
        use_container_width=True,
        disabled=bool(prompt_error),
    )
    exports[1].download_button(
        "下載 JSON",
        data=_json_export(payload, None if selected == _NEW_DRAFT else selected),
        file_name=f"{export_name}.json",
        mime="application/json",
        key=f"{STATE_PREFIX}_export_json",
        use_container_width=True,
        disabled=bool(prompt_error),
    )


__all__ = [
    "ARCHIVE_CONFIRM_STATE_KEY",
    "BOUND_DRAFT_STATE_KEY",
    "GENERATION_ACTION_STATE_KEY",
    "GENERATION_CLUE_STATE_KEY",
    "GENERATION_PROVIDER_STATE_KEY",
    "GENERATION_SOURCE_STATE_KEY",
    "OPENAI_API_KEY_STATE_KEY",
    "OPENAI_CONSENT_STATE_KEY",
    "PAGE_KEY",
    "PAGE_LABEL",
    "PENDING_FORM_STATE_KEY",
    "PROVIDER_GENERATION_HOOK_STATE_KEY",
    "SELECTED_DRAFT_STATE_KEY",
    "render",
]
